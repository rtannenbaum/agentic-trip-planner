# Agentic Trip Planner

A starter Python application using the Google Agent Development Kit (ADK) that generates a trip plan, followed by a human-in-the-loop booking confirmation and execution using a simulated MCP server.

## Project Structure

*   `trip_planner/`: The core agent package. Contains the agent definitions and workflow configuration.
    *   `agent.py`: Defines the trip planner workflow, including the generator agent, the booking extraction/execution agents, and the confirmation flow.
    *   `mcp_server.py`: A simulated booking service (MCP server) that exposes tools to book hotels and activities.
    *   `bookings.json.example`: Template for sample bookings data.
    *   `.env.example`: Template for environment variables (API keys) used by the ADK CLI.
*   `main.py`: A local programmatic test harness to run the workflow in the terminal, handling interrupts and responses.
*   `requirements.txt`: Python dependencies.
*   `.env.example`: Root-level template for environment variables used by `main.py`.

## Prerequisites

If you are running this on a clean gLinux/Cloudtop machine, you may need to install `pip` and the `venv` module first:

```bash
sudo apt update
sudo apt install python3-pip python3-venv
```

## Setup Instructions

1.  **Create and activate a virtual environment**:
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    ```

2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure Environment Variables**:
    You need a Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey).
    
    *   **For running with ADK CLI (Recommended)**:
        Copy the example env file inside the agent folder and add your key:
        ```bash
        cp trip_planner/.env.example trip_planner/.env
        ```
    *   **For running programmatically (`main.py`)**:
        Copy the root example env file and add your key:
        ```bash
        cp .env.example .env
        ```
    
    Open the created `.env` file and replace `your_gemini_api_key_here` with your actual API key:
    ```env
    GOOGLE_API_KEY=AIzaSy...
    ```

4.  **Optionally pre-populate bookings for testing (Optional)**:
    If you want to test the "show my bookings" feature without running a full planning cycle first, you can pre-populate the database with sample bookings:
    ```bash
    cp trip_planner/bookings.json.example trip_planner/bookings.json
    ```

## How to Run the Agent

### Method 1: Using ADK CLI (Recommended & Production Consistent)

This is the recommended way to test the agent locally as it mirrors how the agent is loaded in a production environment (like Vertex AI Reasoning Engine).

Ensure your virtual environment is active, then run:
```bash
adk run trip_planner
```
This will start an interactive chat session in your terminal. Type `exit` to quit.

### Method 2: Programmatically (`main.py`)

If you want to run the agent using a custom Python script (useful for automated testing or integration into a larger application):

Ensure your virtual environment is active, then run:
```bash
python main.py
```

## Workflow Diagram

```mermaid
graph TD
    START([User Input]) --> route_input[Route Input]
    route_input -- "plan_trip" --> trip_generator[Generate Plan]
    route_input -- "query_bookings" --> booking_query_agent[booking_query_agent Node with MCP Tools]
    trip_generator --> present_plan[Present Plan]
    present_plan --> booking_preparer[Extract Bookings]
    booking_preparer --> confirm_booking{confirm_booking Node}
    confirm_booking -- First Run: Yields RequestInput --> PAUSE[PAUSE: Wait for User Reply]
    PAUSE -- User Input --> confirm_booking
    confirm_booking -- Yes --> booking_agent[booking_agent Node with MCP Tools]
    confirm_booking -- No --> cancel_booking[cancel_booking Node]
    booking_agent --> END([END])
    cancel_booking --> END
    booking_query_agent --> END
```
