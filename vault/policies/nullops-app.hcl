# Least privilege for the Flask app: read one secret, nothing else.
# It cannot see nullops/db/root, write secrets, or touch any other mount.

path "nullops/data/db/app" {
  capabilities = ["read"]
}

# Lets the app check the secret's version metadata (useful for debugging rotations)
path "nullops/metadata/db/app" {
  capabilities = ["read"]
}
