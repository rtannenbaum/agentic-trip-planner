"""Local regression evaluation for the trip planner agent.

Usage (from the project root):
    .venv/bin/python run_eval.py

This uses ADK's AgentEvaluator to run the evalset in trip_planner.evalset.json
against the criteria in test_config.json (num_runs=2 by default). It requires a
GEMINI_API_KEY (loaded from .env) because it actually invokes the agent.
"""
import asyncio
import inspect
import os

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# ADK reads message content into traces only when explicitly enabled; harmless here.
os.environ.setdefault("ADK_ENABLE_FEATURES", "PLUGGABLE_AUTH")

from google.adk.evaluation.agent_evaluator import AgentEvaluator


def main():
  # agent_module is imported directly and must expose `root_agent`.
  # trip_planner/agent.py defines root_agent, so the module path is
  # "trip_planner.agent".
  print("Running Agent Evaluator regression suite...")
  result = AgentEvaluator.evaluate(
      agent_module="trip_planner.agent",
      eval_dataset_file_path_or_dir=os.path.join(
          PROJECT_ROOT, "trip_planner.evalset.json"
      ),
      num_runs=1,
      print_detailed_results=True,
  )
  if inspect.isawaitable(result):
    asyncio.run(result)
  print("\n=== SUCCESS: All evaluation cases passed! ===")


if __name__ == "__main__":
  main()
