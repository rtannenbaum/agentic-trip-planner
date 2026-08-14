import asyncio
import json
import logging
import os
from datetime import datetime
try:
  from google.cloud import storage
except ImportError:
  storage = None

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, ToolAnnotations

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("booking_mcp_server")

# Configure OpenTelemetry Tracing if telemetry is enabled
TRACING_ENABLED = os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY") == "true"
if TRACING_ENABLED:
  try:
    from opentelemetry import trace
    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    # Initialize the global TracerProvider
    provider = TracerProvider()
    trace.set_tracer_provider(provider)

    # Export spans directly to Cloud Trace
    cloud_trace_exporter = CloudTraceSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(cloud_trace_exporter))
    
    tracer = trace.get_tracer("booking_mcp_server")
    logger.info("OpenTelemetry distributed tracing configured successfully.")
  except Exception as e:
    logger.warning(f"Failed to initialize OpenTelemetry tracing: {e}")
    TRACING_ENABLED = False
    tracer = None
else:
  tracer = None

server = Server("BookingService")

# Paths
_current_dir = os.path.dirname(os.path.abspath(__file__))
BOOKINGS_FILE = os.path.join(_current_dir, "bookings.json")
SCHEMAS_DIR = os.path.join(_current_dir, "schemas")

# Registry for tool implementation handlers
TOOL_HANDLERS = {}

def register_handler(name: str):
  """Decorator to register a tool implementation handler in TOOL_HANDLERS.

  Args:
      name: The name of the MCP tool to register the handler for.
  """
  def decorator(func):
    TOOL_HANDLERS[name] = func
    return func
  return decorator

# Load Tool Spec helper
def load_tool_spec(filename: str) -> Tool:
  """Loads a tool specification from a unified JSON file.

  Parses the tool name, description, input schema, output schema, and
  metadata annotations from the file and constructs an MCP Tool object.

  Args:
      filename: The name of the JSON spec file under the schemas directory.

  Returns:
      An initialized MCP Tool object.
  """
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
  """Scans the schemas directory and loads all JSON tool specs dynamically.

  Populates the global TOOLS list used by the ListTools handler.
  """
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

def get_bookings_filepath(session_id: str) -> str:
  safe_session_id = "".join([c if c.isalnum() or c in "-_" else "_" for c in session_id])
  return os.path.join(_current_dir, f"bookings_{safe_session_id}.json")

def load_bookings(session_id: str) -> dict:
  """Loads the bookings database for a session from GCS if BUCKET_NAME is set, else local JSON.

  Args:
      session_id: The unique session ID to load bookings for.

  Returns:
      A dictionary containing the bookings data, structured as
      {"hotels": [...], "activities": [...]}.

  Raises:
      RuntimeError: If the database load fails.
  """
  if not session_id:
    raise ValueError("session_id must be provided")

  bucket_name = os.environ.get("BUCKET_NAME")
  blob_name = f"bookings_{session_id}.json"

  if bucket_name and storage:
    logger.info(f"Loading bookings for session {session_id} from GCS bucket: {bucket_name}")
    try:
      gcs_client = storage.Client()
      bucket = gcs_client.bucket(bucket_name)
      blob = bucket.blob(blob_name)
      if blob.exists():
        content = blob.download_as_text()
        return json.loads(content)
      else:
        logger.info(f"{blob_name} not found in GCS bucket. Starting fresh.")
        return {"hotels": [], "activities": []}
    except Exception as e:
      logger.error(f"Error loading bookings from GCS for session {session_id}: {e}")
      raise RuntimeError(f"Failed to load bookings from GCS: {e}")

  local_file = get_bookings_filepath(session_id)
  if os.path.exists(local_file):
    try:
      with open(local_file, "r") as f:
        return json.load(f)
    except Exception as e:
      logger.error(f"Error loading local bookings for session {session_id}: {e}")
      raise RuntimeError(
          f"The bookings database file exists but could not be parsed (Error: {e}). "
          "To prevent further data loss, the server has blocked writes. "
          "Please inform the user that the database is corrupted and check the server logs."
      )
  return {"hotels": [], "activities": []}

def save_bookings(session_id: str, bookings: dict):
  """Saves the bookings database for a session to GCS if BUCKET_NAME is set, else local JSON."""
  if not session_id:
    raise ValueError("session_id must be provided")

  bucket_name = os.environ.get("BUCKET_NAME")
  blob_name = f"bookings_{session_id}.json"

  if bucket_name and storage:
    logger.info(f"Saving bookings for session {session_id} to GCS bucket: {bucket_name}")
    try:
      gcs_client = storage.Client()
      bucket = gcs_client.bucket(bucket_name)
      blob = bucket.blob(blob_name)
      blob.upload_from_string(json.dumps(bookings, indent=2))
      logger.info(f"Saved bookings for session {session_id} to gs://{bucket_name}/{blob_name}")
      return
    except Exception as e:
      logger.error(f"Error saving bookings to GCS for session {session_id}: {e}")
      raise RuntimeError(
          f"Failed to persist bookings to GCS bucket '{bucket_name}' "
          f"(blob '{blob_name}'): {e}"
      )

  local_file = get_bookings_filepath(session_id)
  try:
    with open(local_file, "w") as f:
      json.dump(bookings, f, indent=2)
  except Exception as e:
    logger.error(f"Error saving local bookings for session {session_id}: {e}")

# --- Tool Implementations (Handlers) ---

@register_handler("book_hotel")
async def do_book_hotel(args: dict) -> dict:
  """Simulates booking a hotel.

  Validates dates and appends the booking to the database.

  Args:
      args: Dictionary containing 'hotel_name', 'check_in', 'check_out', and 'session_id'.

  Returns:
      A structured dictionary confirming the booking details.

  Raises:
      ValueError: If check-in or check-out date is not in YYYY-MM-DD format, or session_id is missing.
  """
  session_id = args.get("session_id")
  if not session_id:
    raise ValueError("session_id is required for booking")

  hotel_name = args.get("hotel_name")
  check_in = args.get("check_in")
  check_out = args.get("check_out")
  
  # Semantic date validation
  try:
    in_date = datetime.strptime(check_in, "%Y-%m-%d")
  except ValueError:
    raise ValueError(
        f"Invalid check_in date format or value: '{check_in}'. "
        "Dates must be real calendar dates in 'YYYY-MM-DD' format (e.g., '2026-10-12'). "
        "Please ask the user to provide a valid calendar check-in date."
    )
  try:
    out_date = datetime.strptime(check_out, "%Y-%m-%d")
  except ValueError:
    raise ValueError(
        f"Invalid check_out date format or value: '{check_out}'. "
        "Dates must be real calendar dates in 'YYYY-MM-DD' format (e.g., '2026-10-13'). "
        "Please ask the user to provide a valid calendar check-out date."
    )

  if out_date <= in_date:
    raise ValueError(
        f"Invalid date range: check_out date '{check_out}' must be strictly after check_in date '{check_in}'. "
        "Please ask the user to adjust their checkout date so it falls after the check-in date."
    )
    
  logger.info(f"Booking hotel: {hotel_name} from {check_in} to {check_out} for session {session_id}")
  
  bookings = load_bookings(session_id)
  booking = {
      "hotel_name": hotel_name,
      "check_in": check_in,
      "check_out": check_out,
      "status": "confirmed"
  }
  bookings["hotels"].append(booking)
  save_bookings(session_id, bookings)
  
  return {
      "status": "success",
      "message": f"Successfully booked hotel {hotel_name} from {check_in} to {check_out}.",
      "booking": booking
  }

@register_handler("book_activity")
async def do_book_activity(args: dict) -> dict:
  """Simulates booking an activity.

  Validates date and appends the booking to the database.

  Args:
      args: Dictionary containing 'activity_name', 'date', and 'session_id'.

  Returns:
      A structured dictionary confirming the booking details.

  Raises:
      ValueError: If date is not in YYYY-MM-DD format, or session_id is missing.
  """
  session_id = args.get("session_id")
  if not session_id:
    raise ValueError("session_id is required for booking")

  activity_name = args.get("activity_name")
  date = args.get("date")
  
  try:
    datetime.strptime(date, "%Y-%m-%d")
  except ValueError:
    raise ValueError(
        f"Invalid date format or value: '{date}' for activity '{activity_name}'. "
        "The date must be a real calendar date in 'YYYY-MM-DD' format (e.g., '2026-10-12'). "
        "Please ask the user to clarify or correct the date before retrying the booking."
    )
    
  logger.info(f"Booking activity: {activity_name} on {date} for session {session_id}")
  
  bookings = load_bookings(session_id)
  booking = {
      "activity_name": activity_name,
      "date": date,
      "status": "confirmed"
  }
  bookings["activities"].append(booking)
  save_bookings(session_id, bookings)
  
  return {
      "status": "success",
      "message": f"Successfully booked activity '{activity_name}' on {date}.",
      "booking": booking
  }

@register_handler("list_bookings")
async def do_list_bookings(args: dict) -> dict:
  """Lists all current bookings from the database for a session.

  Args:
      args: Dictionary containing 'session_id'.

  Returns:
      A dictionary containing all loaded hotels and activities bookings.
      
  Raises:
      ValueError: If session_id is missing.
  """
  session_id = args.get("session_id")
  if not session_id:
    raise ValueError("session_id is required to list bookings")

  logger.info(f"Listing bookings for session {session_id}")
  return load_bookings(session_id)

# --- MCP Protocol Handlers ---

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
  """List available tools loaded dynamically from JSON specs.

  Returns:
      A list of loaded MCP Tool objects.
  """
  logger.info("Listing tools")
  return TOOLS


def get_parent_context():
  """Extracts parent OTel trace context from the process environment."""
  if not TRACING_ENABLED:
    return None
  carrier = {}
  for key in ["traceparent", "tracestate"]:
    val = os.environ.get(key) or os.environ.get(key.upper())
    if val:
      carrier[key] = val
  return TraceContextTextMapPropagator().extract(carrier=carrier)

async def _execute_tool(name: str, args: dict, session_id: str | None) -> dict:
  try:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
      raise ValueError(f"Unknown tool: {name}")
    return await handler(args)
  except Exception as e:
    logger.exception(f"Error in handle_call_tool for {name}: {e}")
    raise

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> dict:
  """Handle tool calls by routing them to registered implementation handlers.

  Args:
      name: The name of the tool being called.
      arguments: Parameters passed by the caller.

  Returns:
      The dictionary output returned by the registered handler.

  Raises:
      ValueError: If no handler is registered for the tool name.
  """
  args = arguments or {}
  session_id = args.get("session_id")
  logger.info(f"Calling tool: {name} with args: {arguments}")
  
  parent_context = get_parent_context()
  if TRACING_ENABLED and tracer:
    with tracer.start_as_current_span(f"mcp_tool:{name}", context=parent_context) as span:
      if session_id:
        span.set_attribute("session_id", session_id)
      return await _execute_tool(name, args, session_id)
  else:
    return await _execute_tool(name, args, session_id)

async def main():
  """Runs the stdio MCP server transport loop."""
  async with stdio_server() as (read_stream, write_stream):
    await server.run(
        read_stream,
        write_stream,
        server.create_initialization_options(),
    )

if __name__ == "__main__":
  logger.info("Starting Booking Sim MCP Server (Low-Level Dynamic)...")
  asyncio.run(main())
