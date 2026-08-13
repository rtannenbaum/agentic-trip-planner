from typing import Literal
from google.adk import Agent, Event, Workflow
from pydantic import BaseModel, Field

# Define the evaluation schema for the critic agent
class Evaluation(BaseModel):
  grade: Literal["pass", "fail"] = Field(
      description="Decide if the trip plan meets the traveler's preferences."
  )
  feedback: str = Field(
      description="If the grade is fail, provide detailed feedback on what needs to be improved. If pass, summarize why it is good."
  )

# 1. Input Processing Node
def process_input(node_input: str):
  """Save the user's initial request to state."""
  return Event(state={"trip_details": node_input})

# 2. Trip Generator Agent
# Generates the plan. If feedback is present in state, it refines it.
trip_generator = Agent(
    name="trip_generator",
    model="gemini-3.5-flash",
    description="Generates and refines trip itineraries.",
    instruction=(
        "You are an expert trip generator agent. Your job is to generate a detailed trip itinerary "
        "based on the traveler's preferences (destination, duration, budget, interests). "
        "Traveler preferences: {trip_details}\n\n"
        "If you receive feedback from the critic agent, you MUST refine the itinerary to address "
        "all the points raised, while still respecting the original preferences. "
        "Current feedback to address: {critic_feedback?}"
    ),
    output_key="trip_plan", # Saves the generated plan to state['trip_plan']
)

# 3. Trip Critic Agent
# Evaluates the plan against the preferences.
trip_critic = Agent(
    name="trip_critic",
    model="gemini-3.5-flash",
    description="Evaluates trip itineraries against traveler preferences.",
    instruction=(
        "You are an expert trip critic agent. Your job is to evaluate the generated trip plan "
        "against the traveler's preferences. "
        "Traveler Preferences: {trip_details}\n"
        "Generated Trip Plan: {trip_plan}\n\n"
        "Evaluate if the plan matches the budget, duration, and interests, and if the logistics "
        "(travel times, pacing) are realistic. "
        "If the plan is good and meets all requirements, set grade to 'pass'. "
        "If the plan has issues, set grade to 'fail' and provide detailed, constructive feedback "
        "on what needs to be improved."
    ),
    output_schema=Evaluation,
    output_key="evaluation", # Saves the Evaluation object (dict) to state['evaluation']
)

# 4. Evaluation Processing Node
# Manages loop counter, saves feedback string, and routes.
def process_evaluation(node_input: Evaluation, iteration_count: int = 0):
  next_count = iteration_count + 1
  
  # Limit the loop to prevent infinite refinement loops
  if next_count > 3:
    return Event(
        state={
            "critic_feedback": "Max refinement iterations reached. Finalizing plan.",
            "iteration_count": next_count
        },
        route="pass" # Force exit to presentation
    )
  
  return Event(
      state={
          "critic_feedback": node_input.feedback,
          "iteration_count": next_count
      },
      route=node_input.grade
  )

# 5. Final Presentation Node
def present_plan(trip_plan: str):
  """Present the final refined trip plan to the user."""
  return f"### Final Refined Trip Plan\n\n{trip_plan}"

# Define the workflow (Refinement Loop)
root_agent = Workflow(
    name="trip_planner_workflow",
    edges=[
        (
            "START",
            process_input,
            trip_generator,
            trip_critic,
            process_evaluation,
        ),
        (process_evaluation, {
            "fail": trip_generator, # Loop back to generator
            "pass": present_plan    # Go to final presentation
        }),
    ],
)
