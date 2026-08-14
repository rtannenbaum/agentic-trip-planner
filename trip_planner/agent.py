import datetime
import os
import re
import sys
from typing import Any, Literal
from google.adk import Agent, Context, Event, Workflow
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.workflow import node, RetryConfig
from mcp import StdioServerParameters
from pydantic import BaseModel, Field
from google.genai import types

# 1. Router Schema and Agent
class RouterOutput(BaseModel):
  route: Literal["plan_trip", "query_bookings"] = Field(
      ...,
      description="The route to take. 'plan_trip' to plan a new trip or modify a plan. 'query_bookings' to view bookings, show summaries of booked days, or check booking status."
  )
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
    exceptions=["ClientError"]
)

# Model Configurations (defaults to Vertex AI production models, can be overridden locally via environment)
FLASH_MODEL = os.environ.get("FLASH_MODEL", "gemini-2.5-flash")
PRO_MODEL = os.environ.get("PRO_MODEL", "gemini-2.5-pro")

router_agent = Agent(
    name="router_agent",
    model=FLASH_MODEL,
    description="Routes user input to the correct flow.",
    instruction=(
        "Analyze the user's input and determine if they want to plan a new trip, "
        "or if they are asking about existing/past bookings and itineraries.\n"
        "Select 'plan_trip' if they want to plan a new trip (e.g. destinations, dates, preferences).\n"
        "Select 'query_bookings' if they are asking to see their bookings, "
        "show summaries of booked days (e.g. 'show day 2'), list booked activities, or check booking status.\n\n"
        "You must also copy the user's input query verbatim into the 'query' field."
    ),
    output_schema=RouterOutput,
    output_key="router_output",
)

# 1.5. Input Interceptor Node (to handle stateful interrupts)
@node
def input_router(node_input: str, ctx: Context):
  """Intercepts and routes user input, handling conversational state machine turns."""
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
  """Routes the workflow based on the classified user intent.

  Args:
      router_output: The parsed RouterOutput from router_agent containing the
        classified route and the original user query.

  Returns:
      An Event that updates the 'trip_details' state and routes to 'plan_trip'
      if planning a new trip, or routes to 'query_bookings' if querying
      existing bookings.
  """
  if router_output.route == "plan_trip":
    return Event(state={"trip_details": router_output.query}, route="plan_trip")
  return Event(route="query_bookings")

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

# Configure the MCP server connection
server_params = StdioServerParameters(
    command=sys.executable,
    args=['trip_planner/mcp_server.py'],
    env={
        "BUCKET_NAME": os.environ.get("BUCKET_NAME", ""),
    }
)
mcp_toolset = McpToolset(
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
    tools=[mcp_toolset],
    retry_config=rate_limit_retry_config,
)

# 9. Cancel Booking Node
def cancel_booking():
  """Returns a cancellation message when the user declines to book.

  Returns:
      A string indicating that the booking was cancelled.
  """
  return "Booking cancelled. No reservations were made."

async def get_booking_query_agent_instruction(ctx: Context) -> str:
  session_id = ctx.session.id
  return (
      "You are a helpful assistant. Your job is to answer questions about the traveler's bookings. "
      f"Use the `list_bookings` tool (passing session_id='{session_id}') to retrieve the current bookings and present them to the user. "
      "If no bookings are found, let the user know."
  )

# 10. Booking Query Agent (to list bookings)
booking_query_agent = Agent(
    name="booking_query_agent",
    model=FLASH_MODEL,
    description="Answers questions about bookings.",
    instruction=get_booking_query_agent_instruction,
    tools=[mcp_toolset],
)

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
            "plan_trip": trip_generator,
            "query_bookings": booking_query_agent
        }),
        (trip_generator, present_plan, booking_preparer, serialize_bookings, resolve_booking_dates),
        (resolve_booking_dates, {
            "suspend": suspend_workflow
        })
    ],
)
