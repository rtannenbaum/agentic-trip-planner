import asyncio
import json
import logging
import os
from datetime import datetime
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, ToolAnnotations

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("booking_mcp_server")

server = Server("BookingService")

# Paths
_current_dir = os.path.dirname(os.path.abspath(__file__))
BOOKINGS_FILE = os.path.join(_current_dir, "bookings.json")
SCHEMAS_DIR = os.path.join(_current_dir, "schemas")

# Registry for tool implementation handlers
TOOL_HANDLERS = {}

def register_handler(name: str):
  """Decorator to register a tool implementation handler."""
  def decorator(func):
    TOOL_HANDLERS[name] = func
    return func
  return decorator

# Load Tool Spec helper
def load_tool_spec(filename: str) -> Tool:
  path = os.path.join(SCHEMAS_DIR, filename)
  with open(path, "r") as f:
    data = json.load(f)
    annotations = None
    if "annotations" in data:
      annotations = ToolAnnotations(**data["annotations"])
    return Tool(
        name=data["name"],
        description=data["description"],
        inputSchema=data["inputSchema"],
        outputSchema=data.get("outputSchema"),
        annotations=annotations
    )

# Load all tool specs dynamically on startup
TOOLS = []
def load_all_tools():
  if not os.path.exists(SCHEMAS_DIR):
    logger.warning(f"Schemas directory not found: {SCHEMAS_DIR}")
    return
  for filename in os.listdir(SCHEMAS_DIR):
    if filename.endswith(".json"):
      try:
        tool = load_tool_spec(filename)
        TOOLS.append(tool)
        logger.info(f"Loaded tool spec: {tool.name} from {filename}")
      except Exception as e:
        logger.error(f"Failed to load tool spec {filename}: {e}")

load_all_tools()

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

# --- Tool Implementations (Handlers) ---

@register_handler("book_hotel")
async def do_book_hotel(args: dict) -> dict:
  global BOOKINGS
  hotel_name = args.get("hotel_name")
  check_in = args.get("check_in")
  check_out = args.get("check_out")
  
  # Semantic date validation
  try:
    datetime.strptime(check_in, "%Y-%m-%d")
    datetime.strptime(check_out, "%Y-%m-%d")
  except ValueError:
    raise ValueError("Invalid date values. Must be real calendar dates.")
    
  logger.info(f"Booking hotel: {hotel_name} from {check_in} to {check_out}")
  booking = {
      "hotel_name": hotel_name,
      "check_in": check_in,
      "check_out": check_out,
      "status": "confirmed"
  }
  BOOKINGS["hotels"].append(booking)
  save_bookings()
  
  return {
      "status": "success",
      "message": f"Successfully booked hotel {hotel_name} from {check_in} to {check_out}.",
      "booking": booking
  }

@register_handler("book_activity")
async def do_book_activity(args: dict) -> dict:
  global BOOKINGS
  activity_name = args.get("activity_name")
  date = args.get("date")
  
  try:
    datetime.strptime(date, "%Y-%m-%d")
  except ValueError:
    raise ValueError("Invalid date value. Must be a real calendar date.")
    
  logger.info(f"Booking activity: {activity_name} on {date}")
  booking = {
      "activity_name": activity_name,
      "date": date,
      "status": "confirmed"
  }
  BOOKINGS["activities"].append(booking)
  save_bookings()
  
  return {
      "status": "success",
      "message": f"Successfully booked activity '{activity_name}' on {date}.",
      "booking": booking
  }

@register_handler("list_bookings")
async def do_list_bookings(args: dict) -> dict:
  global BOOKINGS
  logger.info("Listing bookings")
  BOOKINGS = load_bookings()
  return BOOKINGS

# --- MCP Protocol Handlers ---

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
  """List available tools loaded dynamically from JSON specs."""
  logger.info("Listing tools")
  return TOOLS

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> dict:
  """Handle tool calls by routing them to registered implementation handlers."""
  logger.info(f"Calling tool: {name} with args: {arguments}")
  try:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
      raise ValueError(f"Unknown tool: {name}")
    args = arguments or {}
    return await handler(args)
  except Exception as e:
    logger.exception(f"Error in handle_call_tool for {name}: {e}")
    raise

async def main():
  async with stdio_server() as (read_stream, write_stream):
    await server.run(
        read_stream,
        write_stream,
        server.create_initialization_options(),
    )

if __name__ == "__main__":
  logger.info("Starting Booking Sim MCP Server (Low-Level Dynamic)...")
  asyncio.run(main())
