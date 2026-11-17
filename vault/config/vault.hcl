# Vault server config for the NullOps stack.
# Single node with the integrated file storage backend, so secrets survive a restart
# (dev mode keeps everything in memory and would hand out a new DB password on every boot).

ui = true

storage "file" {
  # /vault/file is created and owned by the "vault" user in the official image
  path = "/vault/file"
}

listener "tcp" {
  address = "0.0.0.0:8200"
  # No TLS: Vault is only reachable on the internal "backend" network. A real deployment
  # terminates TLS here (tls_cert_file / tls_key_file) and never listens in the clear.
  tls_disable = true
}

# Address clients are told to use for redirects
api_addr = "http://vault:8200"
