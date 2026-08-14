import datetime
import os
import re
import sys
from typing import Any, Literal
from google.adk import Agent, Context, Event, Workflow
from google.adk.events import RequestInput
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.workflow import node
from mcp import StdioServerParameters
from pydantic import BaseModel, Field

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


router_agent = Agent(
    name="router_agent",
    model="gemini-3.5-flash-lite",
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
    model="gemini-3.5-flash",
    description="Generates trip itineraries.",
    instruction=(
        "You are an expert trip generator agent. Your job is to generate a trip itinerary "
        "based on the traveler's preferences (destination, duration, budget, interests). "
        "Traveler preferences: {trip_details}\n\n"
        "Please provide minimal details for the itinerary: just the name and a one-line description for each activity."
    ),
    output_key="trip_plan",
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
    model="gemini-3.5-flash-lite",
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

# 8. Date Resolution Node (HITL)
@node(rerun_on_resume=True)
def resolve_booking_dates(booking_requests_data: dict | Any, ctx: Context):
  """Checks for relative dates ('Day X') and prompts for actual calendar dates if needed.

  Args:
      booking_requests_data: The extracted BookingRequests (as dict or model).
      ctx: The ADK context object used to retrieve resume inputs.

  Yields:
      RequestInput: If prompting the user for the trip start date.
      Event: With the updated booking_requests_data (with actual dates) to continue.
  """
  if isinstance(booking_requests_data, dict):
    booking_requests_data = BookingRequests(**booking_requests_data)

  # Check if we need the start date to resolve relative dates
  has_relative = False
  if booking_requests_data.hotel:
    if "day" in booking_requests_data.hotel.check_in.lower() or "day" in booking_requests_data.hotel.check_out.lower():
      has_relative = True
  for act in booking_requests_data.activities:
    if "day" in act.date.lower():
      has_relative = True

  start_date = None
  if has_relative:
    start_date_input = ctx.resume_inputs.get("trip_start_date")
    if not start_date_input:
      yield RequestInput(
          interrupt_id="trip_start_date",
          message=(
              "### Trip Start Date Required\n\n"
              "I noticed your itinerary uses relative days (e.g., Day 1, Day 2).\n"
              "To proceed with bookings, I need to know the actual calendar start date.\n\n"
              "Please provide the start date in **YYYY-MM-DD** format (e.g., `2026-08-20`)."
          ),
      )
      return
    start_date = start_date_input.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", start_date):
      yield RequestInput(
          interrupt_id="trip_start_date",
          message=(
              "### Invalid Date Format\n\n"
              f"The provided date `{start_date}` does not match the expected **YYYY-MM-DD** format.\n"
              "Please provide the start date in **YYYY-MM-DD** format (e.g., `2026-08-20`)."
          ),
      )
      return

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

  # Validate and prompt for missing/unresolved dates using RequestInput
  if resolved_hotel:
    # Validate Check-in
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", resolved_hotel.check_in):
      hotel_in_input = ctx.resume_inputs.get("hotel_check_in")
      if not hotel_in_input:
        yield RequestInput(
            interrupt_id="hotel_check_in",
            message=(
                f"### Hotel Check-in Date Required\n\n"
                f"I couldn't resolve the check-in date `{resolved_hotel.check_in}` for **{resolved_hotel.name}**.\n"
                f"Please provide the check-in date in **YYYY-MM-DD** format (e.g., `2026-08-20`)."
            )
        )
        return
      resolved_hotel.check_in = hotel_in_input.strip()
      if not re.match(r"^\d{4}-\d{2}-\d{2}$", resolved_hotel.check_in):
        yield RequestInput(
            interrupt_id="hotel_check_in",
            message=f"Invalid format `{resolved_hotel.check_in}`. Please use **YYYY-MM-DD**."
        )
        return

    # Validate Check-out
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", resolved_hotel.check_out):
      hotel_out_input = ctx.resume_inputs.get("hotel_check_out")
      if not hotel_out_input:
        yield RequestInput(
            interrupt_id="hotel_check_out",
            message=(
                f"### Hotel Check-out Date Required\n\n"
                f"I couldn't resolve the check-out date `{resolved_hotel.check_out}` for **{resolved_hotel.name}**.\n"
                f"Please provide the check-out date in **YYYY-MM-DD** format (e.g., `2026-08-22`)."
            )
        )
        return
      resolved_hotel.check_out = hotel_out_input.strip()
      if not re.match(r"^\d{4}-\d{2}-\d{2}$", resolved_hotel.check_out):
        yield RequestInput(
            interrupt_id="hotel_check_out",
            message=f"Invalid format `{resolved_hotel.check_out}`. Please use **YYYY-MM-DD**."
        )
        return

  # Validate Activities
  for idx, act in enumerate(resolved_activities):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", act.date):
      act_input = ctx.resume_inputs.get(f"activity_date_{idx}")
      if not act_input:
        yield RequestInput(
            interrupt_id=f"activity_date_{idx}",
            message=(
                f"### Activity Date Required\n\n"
                f"I couldn't resolve the date `{act.date}` for the activity **{act.name}**.\n"
                f"Please provide the date for this activity in **YYYY-MM-DD** format."
            )
        )
        return
      act.date = act_input.strip()
      if not re.match(r"^\d{4}-\d{2}-\d{2}$", act.date):
        yield RequestInput(
            interrupt_id=f"activity_date_{idx}",
            message=f"Invalid format `{act.date}`. Please use **YYYY-MM-DD**."
        )
        return

  finalized_requests = BookingRequests(
      hotel=resolved_hotel,
      activities=resolved_activities
  )

  yield Event(state={"booking_requests_data": finalized_requests.model_dump()})

# 9. Booking Confirmation Node (HITL)
@node(rerun_on_resume=True)
def confirm_booking(booking_requests_data: dict | Any, ctx: Context):
  """Prompts the user to confirm the extracted bookings before execution (HITL).

  Args:
      booking_requests_data: The BookingRequests with actual dates (as dict or model).
      ctx: The ADK context object used to retrieve resume inputs.

  Yields:
      RequestInput: If waiting for user reply.
      Event: With output 'confirm' or 'cancel' to route the workflow.
  """
  resume_input = ctx.resume_inputs.get("booking_confirmation")

  if isinstance(booking_requests_data, dict):
    booking_requests_data = BookingRequests(**booking_requests_data)

  # Format structured data for user presentation
  formatted_requests = ""
  if booking_requests_data.hotel:
    h = booking_requests_data.hotel
    formatted_requests += f"*   **Hotel**: {h.name} (Check-in: {h.check_in}, Check-out: {h.check_out})\n"
  if booking_requests_data.activities:
    formatted_requests += "*   **Activities**:\n"
    for act in booking_requests_data.activities:
      formatted_requests += f"    *   {act.name} on {act.date}\n"

  if not resume_input:
    yield RequestInput(
        interrupt_id="booking_confirmation",
        message=(
            f"### Booking Confirmation Required\n\n"
            f"Would you like me to book the following?\n"
            f"{formatted_requests}\n"
            f"Please reply 'yes' to proceed, or 'no' to cancel."
        ),
    )
    return

  if resume_input.strip().lower() in ["yes", "y", "confirm"]:
    yield Event(output="confirm", route="confirm")
  else:
    yield Event(output="cancel", route="cancel")

# Configure the MCP server connection
server_params = StdioServerParameters(
    command=sys.executable,
    args=['trip_planner/mcp_server.py'],
)
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=server_params,
    ),
)

# 9. Booking Execution Agent (with MCP tools)
booking_agent = Agent(
    name="booking_agent",
    model="gemini-3.5-flash",
    description="Executes hotel and activity bookings using MCP tools.",
    instruction=(
        "You are a booking agent. You have access to booking tools via the MCP server.\n"
        "Your task is to book the hotel and activities listed in the booking requests: {booking_requests_data}.\n"
        "Use the `book_hotel` and `book_activity` tools to perform the bookings.\n"
        "After performing the bookings, call `list_bookings` to verify they were recorded, "
        "and present a summary of the confirmed bookings to the user."
    ),
    tools=[mcp_toolset],
)

# 9. Cancel Booking Node
def cancel_booking():
  """Returns a cancellation message when the user declines to book.

  Returns:
      A string indicating that the booking was cancelled.
  """
  return "Booking cancelled. No reservations were made."

# 10. Booking Query Agent (to list bookings)
booking_query_agent = Agent(
    name="booking_query_agent",
    model="gemini-3.5-flash-lite",
    description="Answers questions about bookings.",
    instruction=(
        "You are a helpful assistant. Your job is to answer questions about the traveler's bookings. "
        "Use the `list_bookings` tool to retrieve the current bookings and present them to the user. "
        "If no bookings are found, let the user know."
    ),
    tools=[mcp_toolset],
)

# Define the complete workflow
root_agent = Workflow(
    name="trip_planner_workflow",
    edges=[
        ("START", router_agent),
        (router_agent, execute_route),
        (execute_route, {
            "plan_trip": trip_generator,
            "query_bookings": booking_query_agent
        }),
        (trip_generator, present_plan, booking_preparer, serialize_bookings, resolve_booking_dates),
        (resolve_booking_dates, confirm_booking),
        (confirm_booking, {
            "confirm": booking_agent,
            "cancel": cancel_booking
        })
    ],
)
