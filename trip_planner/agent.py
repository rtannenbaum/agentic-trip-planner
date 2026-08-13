from google.adk import Agent

# Define the main trip planning agent
root_agent = Agent(
    name="trip_planner",
    model="gemini-3.5-flash",
    description="An expert AI agent responsible for planning trips, suggesting itineraries, and helping with travel logistics.",
    instruction=(
        "You are an expert trip planner. Help the user plan their trips. "
        "Ask clarifying questions about their destination, duration, budget, interests, "
        "and who they are traveling with if they don't provide this information. "
        "Suggest detailed itineraries, accommodation options, activities, and dining recommendations. "
        "Be helpful, friendly, and structured in your responses."
    ),
)
