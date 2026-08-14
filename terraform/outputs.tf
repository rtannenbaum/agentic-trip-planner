output "reasoning_engine_id" {
  description = "The ID of the created Reasoning Engine."
  value       = google_vertex_ai_reasoning_engine.test_engine.id
}

output "reasoning_engine_display_name" {
  description = "The display name of the created Reasoning Engine."
  value       = google_vertex_ai_reasoning_engine.test_engine.display_name
}
