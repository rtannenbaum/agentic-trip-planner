import os
import sys
from typing import Literal
from google.adk import Agent, Context, Event, Workflow
from google.adk.events import RequestInput
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.workflow import node
from mcp import StdioServerParameters
from pydantic import BaseModel, Field

# 1. Input Routing Node
def route_input(node_input: str, ctx: Context):
  """Route input to trip generator or booking query agent based on content."""
  query = node_input.lower()
  if "booking" in query and any(w in query for w in ["show", "list", "view", "see", "get", "what", "my"]):
    return Event(route="query_bookings")
  return Event(state={"trip_details": node_input}, route="plan_trip")

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
  """Present the trip plan to the user."""
  return f"### Trip Plan\n\n{trip_plan}"

# 6. Booking Preparer Agent
booking_preparer = Agent(
    name="booking_preparer",
    model="gemini-3.5-flash",
    description="Extracts hotels and activities for booking.",
    instruction=(
        "You are a booking coordinator. Analyze the final trip plan: {trip_plan}\n"
        "Extract the proposed hotel (name, check-in, check-out dates) and all activities "
        "(name, date).\n"
        "Format them as a clear, bulleted list of booking requests. "
        "Do not include any other text."
    ),
    output_key="booking_requests",
)

# 7. Booking Confirmation Node (HITL)
@node(rerun_on_resume=True)
def confirm_booking(booking_requests: str, ctx: Context):
  resume_input = ctx.resume_inputs.get("booking_confirmation")
  if not resume_input:
    yield RequestInput(
        interrupt_id="booking_confirmation",
        message=(
            f"### Booking Confirmation Required\n\n"
            f"Would you like me to book the following?\n"
            f"{booking_requests}\n\n"
            f"Please reply 'yes' to proceed, or 'no' to cancel."
        ),
    )
    return

  if resume_input.strip().lower() in ["yes", "y", "confirm"]:
    yield Event(output="confirm", route="confirm")
  else:
    yield Event(output="cancel", route="cancel")

# Configure the MCP server connection
_current_dir = os.path.dirname(os.path.abspath(__file__))
server_params = StdioServerParameters(
    command=sys.executable,
    args=[os.path.join(_current_dir, 'mcp_server.py')],
)
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=server_params,
    ),
)

# 8. Booking Execution Agent (with MCP tools)
booking_agent = Agent(
    name="booking_agent",
    model="gemini-3.5-flash",
    description="Executes hotel and activity bookings using MCP tools.",
    instruction=(
        "You are a booking agent. You have access to booking tools via the MCP server.\n"
        "Your task is to book the hotel and activities listed in the booking requests: {booking_requests}.\n"
        "Use the `book_hotel` and `book_activity` tools to perform the bookings.\n"
        "After performing the bookings, call `list_bookings` to verify they were recorded, "
        "and present a summary of the confirmed bookings to the user."
    ),
    tools=[mcp_toolset],
)

# 9. Cancel Booking Node
def cancel_booking():
  return "Booking cancelled. No reservations were made."

# 10. Booking Query Agent (to list bookings)
booking_query_agent = Agent(
    name="booking_query_agent",
    model="gemini-3.5-flash",
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
        ("START", route_input),
        (route_input, {
            "plan_trip": trip_generator,
            "query_bookings": booking_query_agent
        }),
        (trip_generator, present_plan, booking_preparer, confirm_booking),
        (confirm_booking, {
            "confirm": booking_agent,
            "cancel": cancel_booking
        })
    ],
)
