output "worker_repository_url" { value = aws_ecr_repository.workers.repository_url }
output "crawler_environment" { value = local.crawler_environment }
output "refresh_environment" { value = local.refresh_environment }
output "worker_names" {
  value = { for name, worker in aws_lambda_function.workers : name => worker.function_name }
}
