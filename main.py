import asyncio
import os
from dotenv import load_dotenv
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from trip_planner.agent import root_agent

# Load environment variables from .env if present
load_dotenv()

async def main():
  """Main entry point for the trip planner agent CLI.

  Initializes the session service, configures the ADK runner with the root
  agent, and enters a REPL loop to process user messages and handle
  Human-In-The-Loop (HITL) booking confirmations.
  """
  # Ensure API key is set
  if not os.environ.get("GOOGLE_API_KEY") and not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_CLOUD_PROJECT"):
    print("Warning: GOOGLE_API_KEY, GEMINI_API_KEY or GOOGLE_CLOUD_PROJECT not set.")
    print("Please set them in your environment or in a .env file.")
    print("If you are using Google AI Studio, set GOOGLE_API_KEY.")
    print("If you are using Vertex AI, set GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION.")

  # Initialize the session service (in-memory for local testing)
  session_service = InMemorySessionService()
  
  # Define default session and user IDs
  session_id = "default_session"
  user_id = "default_user"
  
  # Create the session (awaited)
  await session_service.create_session(
      app_name="trip_planner",
      user_id=user_id,
      session_id=session_id
  )

  # Initialize the runner (with app_name)
  runner = Runner(
      agent=root_agent,
      session_service=session_service,
      app_name="trip_planner"
  )

  print("Trip Planner Agent is ready! Type 'exit' to quit.\n")
  
  pending_interrupt_id = None
  
  while True:
    try:
      # Use asyncio.to_thread to avoid blocking the event loop while waiting for input
      query = await asyncio.to_thread(input, "User: ")
      if query.strip().lower() == "exit":
        print("Goodbye!")
        break
        
      if not query.strip():
        continue

      # Construct the message
      if pending_interrupt_id:
        # If we are responding to an interrupt, send a FunctionResponse
        new_message = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=pending_interrupt_id,
                        name="adk_request_input",
                        response={"result": query}
                    )
                )
            ]
        )
        pending_interrupt_id = None # Clear it
      else:
        # Otherwise, send normal text
        new_message = types.Content(
            role="user",
            parts=[types.Part(text=query)]
        )

      print("Agent: ", end="", flush=True)
      has_printed_anything = False
      
      # Run the agent asynchronously
      async for event in runner.run_async(
          user_id=user_id,
          session_id=session_id,
          new_message=new_message
      ):
        if event.content and event.content.parts:
          for part in event.content.parts:
            # Check for ADK request input interrupt
            if part.function_call and part.function_call.name == "adk_request_input":
              msg = part.function_call.args.get("message", "Input required:")
              if has_printed_anything:
                print()
              print(f"\n--- REQUEST FOR INPUT ---\n{msg}\n-------------------------")
              pending_interrupt_id = part.function_call.id
              break
            elif part.text:
              print(part.text, end="", flush=True)
              has_printed_anything = True
      print("\n") # New line after response
      
    except KeyboardInterrupt:
      print("\nGoodbye!")
      break
    except Exception as e:
      print(f"\nError: {e}\n")

if __name__ == "__main__":
  asyncio.run(main())
