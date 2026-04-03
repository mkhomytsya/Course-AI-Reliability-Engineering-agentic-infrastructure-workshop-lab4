# ==========================================
# Construct k3d cluster
# ==========================================
resource "null_resource" "k3d_cluster" {
  triggers = {
    cluster_name = var.cluster_name
  }

  provisioner "local-exec" {
    command = <<-EOT
      k3d cluster create ${var.cluster_name} \
        --servers 1 \
        --agents 2 \
        -p "80:80@loadbalancer" \
        --k3s-arg '--disable=traefik@server:*'

      echo "Waiting for Kubernetes API server to be ready..."
      for i in $(seq 1 30); do
        if kubectl --context k3d-${var.cluster_name} get nodes >/dev/null 2>&1; then
          echo "API server is ready"
          break
        fi
        echo "Attempt $i/30: API server not ready yet, retrying..."
        sleep 2
      done
    EOT
  }

  provisioner "local-exec" {
    when    = destroy
    command = "k3d cluster delete ${self.triggers.cluster_name}"
  }
}
