# Agentic Trip Planner

A starter Python application using the Google Agent Development Kit (ADK) to build a Trip Planning Agent.

## Project Structure

*   `trip_planner/`: The core agent package. Contains the agent definition and local configuration.
    *   `agent.py`: Defines the `trip_planner` agent instructions and model (`gemini-3.5-flash`).
    *   `.env.example`: Template for environment variables (API keys) used by the ADK CLI.
*   `main.py`: A local programmatic test harness to run the agent in the terminal using the ADK Runner.
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
