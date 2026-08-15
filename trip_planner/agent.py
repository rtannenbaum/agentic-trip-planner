import datetime
import os
import re
import sys
from typing import Any, Literal
from google.adk import Agent, Context, Event, Workflow
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.workflow import node, RetryConfig
import json
import time
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from mcp import StdioServerParameters
from pydantic import BaseModel, Field
from google.genai import types

_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b")
_PHONE_RE = re.compile(r"\b(?:\+\d{1,2}\s?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")

def scrub_pii_string(val: str) -> str:
  """Redacts email, phone, SSN, and Credit Cards from a raw string value."""
  if not val:
    return val
  val = _EMAIL_RE.sub("[REDACTED_EMAIL]", val)
  val = _PHONE_RE.sub("[REDACTED_PHONE]", val)
  val = _SSN_RE.sub("[REDACTED_SSN]", val)
  val = _CC_RE.sub("[REDACTED_CC]", val)
  return val

def scrub_pii(data: Any) -> Any:
  """Recursively traverses nested dictionaries, lists, or primitive types to scrub PII."""
  if isinstance(data, dict):
    return {k: scrub_pii(v) for k, v in data.items()}
  elif isinstance(data, list):
    return [scrub_pii(v) for v in data]
  elif isinstance(data, str):
    return scrub_pii_string(data)
  else:
    return data


class LoggingPreloadMemoryTool(PreloadMemoryTool):
  """Subclass of PreloadMemoryTool that adds structured JSON logging for memory queries."""

  async def process_llm_request(
      self,
      *,
      tool_context,
      llm_request,
  ) -> None:
    user_content = tool_context.user_content
    if (
        not user_content
        or not user_content.parts
        or not user_content.parts[0].text
    ):
      return

    user_query = user_content.parts[0].text
    start_time = time.time()

    # Extract trace context
    from opentelemetry import trace
    current_span = trace.get_current_span()
    trace_id = ""
    span_id = ""
    if current_span.is_recording():
      span_context = current_span.get_span_context()
      trace_id = f"projects/{os.environ.get('GOOGLE_CLOUD_PROJECT', 'tripplanner-dev-sandbox-456240')}/traces/{span_context.trace_id:032x}"
      span_id = f"{span_context.span_id:016x}"

    # Log query initialization
    scrubbed_query = scrub_pii_string(user_query)
    query_log = {
        "severity": "INFO",
        "message": f"Querying long-term memory bank for user query: '{scrubbed_query}'",
        "span_type": "memory_search",
        "query": scrubbed_query,
        "logging.googleapis.com/trace": trace_id,
        "logging.googleapis.com/spanId": span_id,
    }
    print(json.dumps(query_log), file=sys.stderr)

    # Let the base class query the memory bank and update context
    await super().process_llm_request(tool_context=tool_context, llm_request=llm_request)

    # Inspect the updated llm_request to check what memories were injected
    injected_memories = []
    for content in llm_request.contents:
      for part in content.parts or []:
        if part.text and "<PAST_CONVERSATIONS>" in part.text:
          injected_memories.append(part.text)

    scrubbed_memories = [scrub_pii_string(mem) for mem in injected_memories]
    duration_ms = (time.time() - start_time) * 1000
    result_log = {
        "severity": "INFO",
        "message": f"Memory bank search completed in {duration_ms:.2f}ms. Injected {len(scrubbed_memories)} memory contexts.",
        "span_type": "memory_result",
        "duration_ms": duration_ms,
        "injected_memories": scrubbed_memories,
        "logging.googleapis.com/trace": trace_id,
        "logging.googleapis.com/spanId": span_id,
    }
    print(json.dumps(result_log), file=sys.stderr)

preload_memory = LoggingPreloadMemoryTool()

class RouterOutput(BaseModel):
  query: str = Field(..., description="Copy the user's input query here verbatim.")

# Schemas for Structured Booking Extraction
class HotelBookingRequest(BaseModel):
  name: str = Field(..., description="Name of the hotel.")
  check_in: str = Field(
      ...,
      description="Check-in date in YYYY-MM-DD format. If start date is unknown, output 'Day 1' or similar relative day."
  )
  check_out: str = Field(
      ...,
      description="Check-out date in YYYY-MM-DD format. If start date is unknown, output 'Day X' or similar relative day."
  )

class ActivityBookingRequest(BaseModel):
  name: str = Field(..., description="Name of the activity.")
  date: str = Field(
      ...,
      description="Date of the activity in YYYY-MM-DD format. If start date is unknown, output 'Day X' or similar relative day."
  )

class BookingRequests(BaseModel):
  hotel: HotelBookingRequest | None = Field(default=None, description="Hotel booking request if any.")
  activities: list[ActivityBookingRequest] = Field(
      default_factory=list,
      description="List of activity booking requests."
  )

rate_limit_retry_config = RetryConfig(
    max_attempts=4,
    initial_delay=3.0,
    backoff_factor=2.0,
    exceptions=["ResourceExhausted", "ServerError", "ServiceUnavailable"]
)

# Model Configurations (defaults to Vertex AI production models, can be overridden locally via environment)
FLASH_MODEL = os.environ.get("FLASH_MODEL", "gemini-2.5-flash")
PRO_MODEL = os.environ.get("PRO_MODEL", "gemini-2.5-pro")

router_agent = Agent(
    name="router_agent",
    model=FLASH_MODEL,
    description="Captures user trip planning input.",
    instruction=(
        "Copy the user's input query verbatim into the 'query' field."
    ),
    output_schema=RouterOutput,
    output_key="router_output",
)

# 1.5. Input Interceptor Node (to handle stateful interrupts)
@node
def input_router(node_input: str, ctx: Context):
  """Intercepts and routes user input, handling conversational state machine turns."""
  from opentelemetry import trace
  current_span = trace.get_current_span()
  if current_span.is_recording():
    current_span.set_attribute("session_id", ctx.session.id)
    current_span.set_attribute("conversation_id", ctx.session.id)
    current_span.set_attribute("user_prompt", node_input)

  awaiting_input = ctx.state.get("awaiting_input")
  
  if awaiting_input:
    input_type = awaiting_input.get("type")
    original_data = awaiting_input.get("original_data")
    
    if input_type == "booking_confirmation":
      cleaned = node_input.strip().lower()
      if cleaned in ["yes", "y", "confirm", "go ahead"]:
        # Set booking requests data and clear awaiting input
        return Event(
            state={"booking_requests_data": original_data, "awaiting_input": None}, 
            route="confirm"
        )
      elif cleaned in ["no", "n", "cancel", "stop"]:
        return Event(
            state={"awaiting_input": None}, 
            route="cancel"
        )
      else:
        return Event(
            output="Please reply with 'yes' to confirm the booking, or 'no' to cancel.",
            route="keep_prompting"
        )
        
    elif input_type == "trip_start_date":
      start_date = node_input.strip()
      if re.match(r"^\d{4}-\d{2}-\d{2}$", start_date):
        return Event(
            state={
                "trip_start_date": start_date,
                "booking_requests_data": original_data,
                "awaiting_input": None
            },
            route="resume_date_resolution"
        )
      else:
        return Event(
            output=(
                f"The provided date `{start_date}` does not match the expected **YYYY-MM-DD** format.\n"
                "Please provide the start date in **YYYY-MM-DD** format (e.g., `2026-08-20`)."
            ),
            route="keep_prompting"
        )

    elif input_type == "hotel_check_in":
      val = node_input.strip()
      if re.match(r"^\d{4}-\d{2}-\d{2}$", val):
        if "hotel" in original_data and original_data["hotel"]:
          original_data["hotel"]["check_in"] = val
        return Event(
            state={"booking_requests_data": original_data, "awaiting_input": None},
            route="resume_date_resolution"
        )
      else:
        return Event(
            output=f"Invalid format `{val}`. Please provide check-in date in **YYYY-MM-DD** format.",
            route="keep_prompting"
        )

    elif input_type == "hotel_check_out":
      val = node_input.strip()
      if re.match(r"^\d{4}-\d{2}-\d{2}$", val):
        if "hotel" in original_data and original_data["hotel"]:
          original_data["hotel"]["check_out"] = val
        return Event(
            state={"booking_requests_data": original_data, "awaiting_input": None},
            route="resume_date_resolution"
        )
      else:
        return Event(
            output=f"Invalid format `{val}`. Please provide check-out date in **YYYY-MM-DD** format.",
            route="keep_prompting"
        )

    elif input_type.startswith("activity_date_"):
      idx = awaiting_input.get("extra", {}).get("index")
      val = node_input.strip()
      if re.match(r"^\d{4}-\d{2}-\d{2}$", val) and idx is not None:
        if "activities" in original_data and len(original_data["activities"]) > idx:
          original_data["activities"][idx]["date"] = val
        return Event(
            state={"booking_requests_data": original_data, "awaiting_input": None},
            route="resume_date_resolution"
        )
      else:
        return Event(
            output=f"Invalid format `{val}`. Please use **YYYY-MM-DD**.",
            route="keep_prompting"
        )

  # Default flow: pass input along to router_agent
  return Event(output=node_input, route="plan_normally")

def present_message(node_input: str):
  """Node that returns a message to the user."""
  return types.Content(parts=[types.Part(text=node_input)])

def suspend_workflow(node_input: str):
  """Terminal node that just returns the output to suspend execution."""
  return types.Content(parts=[types.Part(text=node_input)])

def execute_route(router_output: RouterOutput):
  """Routes the workflow to trip_generator with user trip_details.

  Args:
      router_output: The parsed RouterOutput from router_agent containing the
        user query.

  Returns:
      An Event that updates the 'trip_details' state and routes to 'plan_trip'.
  """
  return Event(state={"trip_details": router_output.query}, route="plan_trip")

# 2. Trip Generator Agent
trip_generator = Agent(
    name="trip_generator",
    model=PRO_MODEL,
    description="Generates trip itineraries.",
    instruction=(
        "You are an expert trip generator agent. Your job is to generate a trip itinerary "
        "based on the traveler's preferences (destination, duration, budget, interests). "
        "Traveler preferences: {trip_details}\n\n"
        "Please provide minimal details for the itinerary: just the name and a one-line description for each activity."
    ),
    tools=[preload_memory],
    output_key="trip_plan",
    retry_config=rate_limit_retry_config,
)

# 3. Final Presentation Node
def present_plan(trip_plan: str):
  """Formats and wraps the generated trip plan in a user-facing markdown header.

  Args:
      trip_plan: The raw text representation of the generated itinerary.

  Returns:
      A formatted markdown string presenting the trip plan.
  """
  return f"### Trip Plan\n\n{trip_plan}"

# 6. Booking Preparer Agent
booking_preparer = Agent(
    name="booking_preparer",
    model=FLASH_MODEL,
    description="Extracts hotels and activities for booking into a structured format.",
    instruction=(
        "You are a booking coordinator. Analyze the final trip plan: {trip_plan}\n"
        "Extract the proposed hotel and all activities.\n"
        "If no hotel is proposed in the itinerary (e.g. for a day trip or if the traveler is staying elsewhere), set the 'hotel' field to null.\n"
        "If the start date is unknown, extract dates as relative days (e.g., 'Day 1' for the first day, 'Day 2' for the second day).\n"
        "Otherwise, output dates in 'YYYY-MM-DD' format."
    ),
    output_schema=BookingRequests,
    output_key="booking_requests_data",
)

# Date Parsing Helper
def parse_relative_date(relative_str: str, start_date_str: str) -> str:
  """Parses 'Day X' relative to start_date_str (YYYY-MM-DD) and returns YYYY-MM-DD.

  If the string is already a YYYY-MM-DD, returns it verbatim.
  """
  if re.match(r"^\d{4}-\d{2}-\d{2}$", relative_str):
    return relative_str

  match = re.search(r"day\s*(\d+)", relative_str, re.IGNORECASE)
  if match:
    day_num = int(match.group(1))
    try:
      start_dt = datetime.datetime.strptime(start_date_str, "%Y-%m-%d")
      target_dt = start_dt + datetime.timedelta(days=(day_num - 1))
      return target_dt.strftime("%Y-%m-%d")
    except ValueError as e:
      raise ValueError(
          f"Invalid start date format '{start_date_str}'. Expected YYYY-MM-DD. Error: {e}"
      )

  return relative_str

# 7. Serialize Bookings Node
@node
def serialize_bookings(booking_requests_data: BookingRequests):
  """Converts Pydantic BookingRequests to dict to avoid serialization issues."""
  return Event(state={"booking_requests_data": booking_requests_data.model_dump()})

# 8. Date Resolution Node (State-Machine)
@node
def resolve_booking_dates(booking_requests_data: dict | Any, ctx: Context):
  """Checks for relative dates ('Day X') and prompts for actual calendar dates if needed.

  Uses state variables instead of platform interruptions.

  Args:
      booking_requests_data: The extracted BookingRequests (as dict or model).
      ctx: The ADK context object containing state.

  Returns:
      Event: Routes to 'suspend' with prompting message if input is needed,
             otherwise returns the finalized BookingRequests.
  """
  if isinstance(booking_requests_data, dict):
    booking_requests_data = BookingRequests(**booking_requests_data)

  start_date = ctx.state.get("trip_start_date")

  # Check if we need the start date to resolve relative dates
  has_relative = False
  if booking_requests_data.hotel:
    if "day" in booking_requests_data.hotel.check_in.lower() or "day" in booking_requests_data.hotel.check_out.lower():
      has_relative = True
  for act in booking_requests_data.activities:
    if "day" in act.date.lower():
      has_relative = True

  if has_relative and not start_date:
    msg = (
        "### Trip Start Date Required\n\n"
        "I noticed your itinerary uses relative days (e.g., Day 1, Day 2).\n"
        "To proceed with bookings, I need to know the actual calendar start date.\n\n"
        "Please provide the start date in **YYYY-MM-DD** format (e.g., `2026-08-20`)."
    )
    return Event(
        output=msg,
        state={
            "awaiting_input": {
                "type": "trip_start_date",
                "original_data": booking_requests_data.model_dump()
            }
        },
        route="suspend"
    )

  # Resolve relative dates where possible
  resolved_hotel = None
  if booking_requests_data.hotel:
    in_date = booking_requests_data.hotel.check_in
    out_date = booking_requests_data.hotel.check_out
    if start_date:
      in_date = parse_relative_date(in_date, start_date)
      out_date = parse_relative_date(out_date, start_date)
    resolved_hotel = HotelBookingRequest(
        name=booking_requests_data.hotel.name,
        check_in=in_date,
        check_out=out_date
    )

  resolved_activities = []
  for act in booking_requests_data.activities:
    act_date = act.date
    if start_date:
      act_date = parse_relative_date(act_date, start_date)
    resolved_activities.append(
        ActivityBookingRequest(name=act.name, date=act_date)
    )

  # Validate and prompt for missing/unresolved dates using state variables
  if resolved_hotel:
    # Validate Check-in
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", resolved_hotel.check_in):
      msg = (
          f"### Hotel Check-in Date Required\n\n"
          f"I couldn't resolve the check-in date `{resolved_hotel.check_in}` for **{resolved_hotel.name}**.\n"
          f"Please provide the check-in date in **YYYY-MM-DD** format (e.g., `2026-08-20`)."
      )
      return Event(
          output=msg,
          state={
              "awaiting_input": {
                  "type": "hotel_check_in",
                  "original_data": BookingRequests(hotel=resolved_hotel, activities=resolved_activities).model_dump()
              }
          },
          route="suspend"
      )

    # Validate Check-out
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", resolved_hotel.check_out):
      msg = (
          f"### Hotel Check-out Date Required\n\n"
          f"I couldn't resolve the check-out date `{resolved_hotel.check_out}` for **{resolved_hotel.name}**.\n"
          f"Please provide the check-out date in **YYYY-MM-DD** format (e.g., `2026-08-22`)."
      )
      return Event(
          output=msg,
          state={
              "awaiting_input": {
                  "type": "hotel_check_out",
                  "original_data": BookingRequests(hotel=resolved_hotel, activities=resolved_activities).model_dump()
              }
          },
          route="suspend"
      )

  # Validate Activities
  for idx, act in enumerate(resolved_activities):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", act.date):
      msg = (
          f"### Activity Date Required\n\n"
          f"I couldn't resolve the date `{act.date}` for the activity **{act.name}**.\n"
          f"Please provide the date for this activity in **YYYY-MM-DD** format."
      )
      return Event(
          output=msg,
          state={
              "awaiting_input": {
                  "type": f"activity_date_{idx}",
                  "original_data": BookingRequests(hotel=resolved_hotel, activities=resolved_activities).model_dump(),
                  "extra": {"index": idx}
              }
          },
          route="suspend"
      )

  finalized_requests = BookingRequests(
      hotel=resolved_hotel,
      activities=resolved_activities
  )

  # Format structured data for user presentation
  formatted_requests = ""
  if finalized_requests.hotel:
    h = finalized_requests.hotel
    formatted_requests += f"*   **Hotel**: {h.name} (Check-in: {h.check_in}, Check-out: {h.check_out})\n"
  if finalized_requests.activities:
    formatted_requests += "*   **Activities**:\n"
    for act in finalized_requests.activities:
      formatted_requests += f"    *   {act.name} on {act.date}\n"

  if not finalized_requests.hotel and not finalized_requests.activities:
    return Event(output="There are no bookings to confirm.", route="suspend")

  msg = (
      f"### Booking Confirmation Required\n\n"
      f"Would you like me to book the following?\n"
      f"{formatted_requests}\n"
      f"Please reply 'yes' to proceed, or 'no' to cancel."
  )

  return Event(
      output=msg,
      state={
          "awaiting_input": {
              "type": "booking_confirmation",
              "original_data": finalized_requests.model_dump()
          }
      },
      route="suspend"
  )

def get_mcp_env() -> dict[str, str]:
  mcp_env = {
      "BUCKET_NAME": os.environ.get("BUCKET_NAME", ""),
      "PYTHONPATH": os.environ.get("PYTHONPATH", os.path.pathsep.join(sys.path)),
  }
  # Forward telemetry & credentials variables for distributed tracing
  prefixes_to_forward = ("OTEL_", "TRACE", "GOOGLE_CLOUD_")
  for key, val in os.environ.items():
    if key.startswith(prefixes_to_forward):
      mcp_env[key] = val
  return mcp_env

class DynamicMcpToolset(McpToolset):
  def __setstate__(self, state):
    super().__setstate__(state)
    import inspect
    import os
    import sys
    from google.adk.tools.mcp_tool.mcp_session_manager import MCPSessionManager, StdioConnectionParams, StdioServerParameters
    
    agent_dir = os.path.dirname(os.path.abspath(__file__))
    mcp_server_path = os.path.join(agent_dir, "mcp_server.py")
    if not os.path.exists(mcp_server_path):
      mcp_server_path = "trip_planner/mcp_server.py"

    cmd = sys.executable or "python3"

    mcp_env = get_mcp_env()
    if hasattr(self, '_connection_params'):
      params = self._connection_params
      if isinstance(params, StdioConnectionParams):
        params.server_params = StdioServerParameters(
            command=cmd,
            args=[mcp_server_path],
            env=mcp_env
        )
      elif isinstance(params, StdioServerParameters):
        self._connection_params = StdioServerParameters(
            command=cmd,
            args=[mcp_server_path],
            env=mcp_env
        )
      
      init_params = inspect.signature(MCPSessionManager.__init__).parameters
      kwargs = {
          "connection_params": self._connection_params,
          "errlog": getattr(self, '_errlog', sys.stderr),
          "sampling_callback": getattr(self, '_sampling_callback', None),
          "sampling_capabilities": getattr(self, '_sampling_capabilities', None),
          "elicitation_callback": getattr(self, '_elicitation_callback', None),
      }
      filtered_kwargs = {k: v for k, v in kwargs.items() if k in init_params}
      self._mcp_session_manager = MCPSessionManager(**filtered_kwargs)

# Configure the MCP server connection using relative path for container portability
_MCP_SERVER_PATH = "trip_planner/mcp_server.py"

server_params = StdioServerParameters(
    command="python3",
    args=[_MCP_SERVER_PATH],
    env=get_mcp_env()
)
mcp_toolset = DynamicMcpToolset(
    connection_params=StdioConnectionParams(
        server_params=server_params,
    ),
)

async def get_booking_agent_instruction(ctx: Context) -> str:
  booking_requests_data = ctx.state.get("booking_requests_data", "")
  session_id = ctx.session.id
  return (
      "You are a booking agent. You have access to booking tools.\n"
      f"Your task is to book the hotel and activities listed in the booking requests: {booking_requests_data}.\n"
      "Use the `book_hotel` and `book_activity` tools to perform the bookings.\n"
      "When calling `book_activity`, map the activity name to the `activity_name` parameter.\n"
      f"The current session ID is '{session_id}'. You MUST pass this session_id to all book_hotel and book_activity tool calls.\n"
      f"After performing the bookings, call `list_bookings` (passing session_id='{session_id}') to verify they were recorded, "
      "and present a summary of the confirmed bookings to the user."
  )

# 9. Booking Execution Agent (with MCP tools)
booking_agent = Agent(
    name="booking_agent",
    model=FLASH_MODEL,
    description="Executes hotel and activity bookings using MCP tools.",
    instruction=get_booking_agent_instruction,
    tools=[mcp_toolset, preload_memory],
    retry_config=rate_limit_retry_config,
)

# 9. Cancel Booking Node
def cancel_booking():
  """Returns a cancellation message when the user declines to book.

  Returns:
      A string indicating that the booking was cancelled.
  """
  return "Booking cancelled. No reservations were made."

# Define the complete workflow
root_agent = Workflow(
    name="trip_planner_workflow",
    edges=[
        ("START", input_router),
        (input_router, {
            "plan_normally": router_agent,
            "confirm": booking_agent,
            "cancel": cancel_booking,
            "keep_prompting": present_message,
            "resume_date_resolution": resolve_booking_dates
        }),
        (router_agent, execute_route),
        (execute_route, {
            "plan_trip": trip_generator
        }),
        (trip_generator, present_plan, booking_preparer, serialize_bookings, resolve_booking_dates),
        (resolve_booking_dates, {
            "suspend": suspend_workflow
        })
    ],
)

# 11. Custom AdkApp subclass for Telemetry Session Correlation
from vertexai.preview.reasoning_engines import AdkApp

class DynamicAdkApp(AdkApp):
  """Custom AdkApp subclass that intercepts query requests to correlate OTel spans to playground session IDs."""
  
  def __init__(self, *, agent, events_compaction_config=None, **kwargs):
    super().__init__(agent=agent, **kwargs)
    self._tmpl_attrs["events_compaction_config"] = events_compaction_config

  def project_id(self) -> str:
    """Override project_id to avoid calling Resource Manager API which fails during setup."""
    return self._tmpl_attrs.get("project") or os.environ.get("GOOGLE_CLOUD_PROJECT", "tripplanner-dev-sandbox-456240")

  def set_up(self):
    # Dynamically patch google.auth.default to restrict credentials scopes.
    # This prevents the container from trying to refresh tokens with local-developer Workspace scopes
    # (Gmail, Calendar, Drive, YouTube) which are not supported by the VM metadata server.
    import google.auth
    
    original_default = google.auth.default
    
    def patched_default(*args, **kwargs):
      # Restrict scopes to cloud-platform only.
      kwargs["scopes"] = ["https://www.googleapis.com/auth/cloud-platform"]
      if "default_scopes" in kwargs:
        kwargs["default_scopes"] = ["https://www.googleapis.com/auth/cloud-platform"]
      return original_default(*args, **kwargs)
      
    google.auth.default = patched_default

    # Dynamically update server_params command to current container sys.executable
    server_params.command = sys.executable

    # Ensure GOOGLE_CLOUD_AGENT_ENGINE_ID is set so VertexAiMemoryBankService is initialized
    if "GOOGLE_CLOUD_AGENT_ENGINE_ID" not in os.environ:
      os.environ["GOOGLE_CLOUD_AGENT_ENGINE_ID"] = "8809438344590131200"

    # Call base setup to populate services and config environment variables
    super().set_up()
    
    # Override Runner instantiation to pass App with compaction config!
    from google.adk.runners import Runner
    from google.adk.apps.app import App
    
    app_name = self._tmpl_attrs.get("app_name") or "default-app-name"
    # App name must start with a letter and contain only alphanumeric, underscores, and hyphens.
    # GCP sets GOOGLE_CLOUD_AGENT_ENGINE_ID (which is a number) as the app_name, triggering pydantic validation errors.
    if app_name and not app_name[0].isalpha():
      app_name = f"app-{app_name}"

    app_config = App(
        name=app_name,
        root_agent=self._tmpl_attrs.get("agent"),
        plugins=self._tmpl_attrs.get("plugins") or [],
        events_compaction_config=self._tmpl_attrs.get("events_compaction_config")
    )
    
    self._tmpl_attrs["runner"] = Runner(
        app=app_config,
        session_service=self._tmpl_attrs.get("session_service"),
        artifact_service=self._tmpl_attrs.get("artifact_service"),
        memory_service=self._tmpl_attrs.get("memory_service"),
        auto_create_session=True,
    )
    
    self._tmpl_attrs["in_memory_runner"] = Runner(
        app=app_config,
        session_service=self._tmpl_attrs.get("in_memory_session_service"),
        artifact_service=self._tmpl_attrs.get("in_memory_artifact_service"),
        memory_service=self._tmpl_attrs.get("in_memory_memory_service"),
        credential_service=self._tmpl_attrs.get("credential_service"),
    )

  def stream_query(self, *, message, user_id, session_id=None, run_config=None, **kwargs):
    from opentelemetry import trace
    import json
    import logging
    
    current_span = trace.get_current_span()
    s_id = session_id
    if not s_id and run_config and isinstance(run_config, dict):
      s_id = run_config.get("session_id")
      
    if s_id and current_span.is_recording():
      current_span.set_attribute("session_id", s_id)
      current_span.set_attribute("conversation_id", s_id)
      if isinstance(message, str):
        current_span.set_attribute("user_prompt", message)
      elif isinstance(message, dict):
        current_span.set_attribute("user_prompt", json.dumps(message))
      logging.getLogger("booking_mcp_server").info(f"Trace correlated to session: {s_id}")
      
    yield from super().stream_query(
        message=message,
        user_id=user_id,
        session_id=session_id,
        run_config=run_config,
        **kwargs
    )

  async def async_stream_query(self, *, message, user_id, session_id=None, run_config=None, **kwargs):
    from opentelemetry import trace
    import json
    import logging
    
    current_span = trace.get_current_span()
    s_id = session_id
    if not s_id and run_config and isinstance(run_config, dict):
      s_id = run_config.get("session_id")
      
    if s_id and current_span.is_recording():
      current_span.set_attribute("session_id", s_id)
      current_span.set_attribute("conversation_id", s_id)
      if isinstance(message, str):
        current_span.set_attribute("user_prompt", message)
      elif isinstance(message, dict):
        current_span.set_attribute("user_prompt", json.dumps(message))
      logging.getLogger("booking_mcp_server").info(f"Trace correlated to session: {s_id}")
      
    async for event in super().async_stream_query(
        message=message,
        user_id=user_id,
        session_id=session_id,
        run_config=run_config,
        **kwargs
    ):
      yield event

# 12. Structured JSON Logging Plugin for GCP Trace Timeline Correlation
from google.adk.plugins.base_plugin import BasePlugin

class StructuredLoggingPlugin(BasePlugin):
  """Intercepts LLM and tool calls to output structured JSON logs to stderr for Cloud Logging ingestion."""
  
  def __init__(self):
    super().__init__(name="structured_logging_plugin")

  def _get_trace_context(self) -> dict:
    from opentelemetry import trace
    current_span = trace.get_current_span()
    context = current_span.get_span_context()
    if context.is_valid:
      project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "unknown-project")
      trace_id_hex = f"{context.trace_id:032x}"
      span_id_hex = f"{context.span_id:016x}"
      return {
          "logging.googleapis.com/trace": f"projects/{project_id}/traces/{trace_id_hex}",
          "logging.googleapis.com/spanId": span_id_hex,
          "logging.googleapis.com/trace_sampled": context.trace_flags.sampled
      }
    return {}

  def _log_structured(self, severity: str, message: str, payload: dict):
    import json
    log_record = {
        "severity": severity,
        "message": message,
        **self._get_trace_context(),
        **payload
    }
    # Print raw JSON directly to stderr which is parsed by GCP container log agent
    print(json.dumps(log_record), file=sys.stderr)

  async def before_model_callback(self, *, callback_context, llm_request):
    is_observation = False
    if llm_request.contents:
      last_msg = llm_request.contents[-1]
      if last_msg.parts:
        for part in last_msg.parts:
          if part.function_response:
            is_observation = True
            break

    span_type = "observation" if is_observation else "llm_call"
    self._log_structured(
        severity="INFO",
        message=f"Model Inference ({span_type}) started",
        payload={
            "span_type": span_type,
            "status": "started",
            "model_name": llm_request.model,
            "prompt_length_messages": len(llm_request.contents)
        }
    )

  async def after_model_callback(self, *, callback_context, llm_response):
    usage = getattr(llm_response, 'usage_metadata', None)
    usage_dict = {}
    if usage:
      usage_dict = {
          "prompt_tokens": getattr(usage, 'prompt_token_count', 0),
          "candidates_tokens": getattr(usage, 'candidates_token_count', 0),
          "total_tokens": getattr(usage, 'total_token_count', 0)
      }
      
    function_calls = []
    if llm_response.content and llm_response.content.parts:
      for part in llm_response.content.parts:
        if part.function_call:
          function_calls.append(part.function_call.name)
              
    self._log_structured(
        severity="INFO",
        message="Model Inference completed",
        payload={
            "span_type": "llm_call",
            "status": "completed",
            "usage": usage_dict,
            "function_calls": function_calls,
            "has_text_response": bool(llm_response.text if hasattr(llm_response, 'text') else False)
        }
    )

  async def before_tool_callback(self, *, tool, tool_args, tool_context):
    self._log_structured(
        severity="INFO",
        message=f"Tool execution started: {tool.name}",
        payload={
            "span_type": "tool_execution",
            "status": "started",
            "tool_name": tool.name,
            "arguments": scrub_pii(tool_args)
        }
    )

  async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
    self._log_structured(
        severity="INFO",
        message=f"Tool execution completed: {tool.name}",
        payload={
            "span_type": "tool_execution",
            "status": "completed",
            "tool_name": tool.name,
            "result_summary": scrub_pii_string(str(result)[:500])
        }
    )

  async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
    self._log_structured(
        severity="ERROR",
        message=f"Tool execution failed: {tool.name}",
        payload={
            "span_type": "tool_execution",
            "status": "failed",
            "tool_name": tool.name,
            "error_type": type(error).__name__,
            "error_message": scrub_pii_string(str(error))
        }
    )
