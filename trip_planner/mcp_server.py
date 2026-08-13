import json
import logging
import os
from fastmcp import FastMCP

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("booking_mcp_server")

mcp = FastMCP("BookingService")

BOOKINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bookings.json")

def load_bookings():
  if os.path.exists(BOOKINGS_FILE):
    try:
      with open(BOOKINGS_FILE, "r") as f:
        return json.load(f)
    except Exception as e:
      logger.error(f"Error loading bookings: {e}")
  return {"hotels": [], "activities": []}

BOOKINGS = load_bookings()

def save_bookings():
  try:
    with open(BOOKINGS_FILE, "w") as f:
      json.dump(BOOKINGS, f, indent=2)
  except Exception as e:
    logger.error(f"Error saving bookings: {e}")

@mcp.tool
def book_hotel(hotel_name: str, check_in: str, check_out: str) -> str:
  """Simulates booking a hotel.
  
  Args:
    hotel_name: Name of the hotel to book.
    check_in: Check-in date (YYYY-MM-DD).
    check_out: Check-out date (YYYY-MM-DD).
  """
  logger.info(f"Booking hotel: {hotel_name} from {check_in} to {check_out}")
  booking = {
      "hotel_name": hotel_name,
      "check_in": check_in,
      "check_out": check_out,
      "status": "confirmed"
  }
  BOOKINGS["hotels"].append(booking)
  save_bookings()
  return f"Successfully booked hotel {hotel_name} from {check_in} to {check_out}."

@mcp.tool
def book_activity(activity_name: str, date: str) -> str:
  """Simulates booking an activity.
  
  Args:
    activity_name: Name of the activity to book.
    date: Date of the activity (YYYY-MM-DD).
  """
  logger.info(f"Booking activity: {activity_name} on {date}")
  booking = {
      "activity_name": activity_name,
      "date": date,
      "status": "confirmed"
  }
  BOOKINGS["activities"].append(booking)
  save_bookings()
  return f"Successfully booked activity '{activity_name}' on {date}."

@mcp.tool
def list_bookings() -> str:
  """Lists all current bookings."""
  logger.info("Listing bookings")
  global BOOKINGS
  BOOKINGS = load_bookings()
  
  if not BOOKINGS["hotels"] and not BOOKINGS["activities"]:
    return "No bookings found."
  
  result = "Current Bookings:\n"
  if BOOKINGS["hotels"]:
    result += "\nHotels:\n"
    for b in BOOKINGS["hotels"]:
      result += f" - {b['hotel_name']} ({b['check_in']} to {b['check_out']}) - Status: {b['status']}\n"
  if BOOKINGS["activities"]:
    result += "\nActivities:\n"
    for b in BOOKINGS["activities"]:
      result += f" - {b['activity_name']} on {b['date']} - Status: {b['status']}\n"
  return result

if __name__ == "__main__":
  logger.info("Starting Booking Sim MCP Server...")
  mcp.run()
