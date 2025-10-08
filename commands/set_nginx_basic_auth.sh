#!/bin/bash

set -e

# Check if API_USER and API_PASSWORD are set
if [ -z "$API_USER" ] || [ -z "$API_PASSWORD" ]; then
    echo "ERROR: API_USER and API_PASSWORD must be set."
    exit 1
fi

# Create a .htpasswd file
echo "Creating .htpasswd file."
# Use -c only when creating a file for the first time
if [ ! -f /etc/nginx/.htpasswd ]; then
    htpasswd -cb /etc/nginx/.htpasswd "$API_USER" "$API_PASSWORD"
else
    htpasswd -b /etc/nginx/.htpasswd "$API_USER" "$API_PASSWORD"
fi

# Create auth.conf file to enable Basic Auth
cat <<EOL > /etc/nginx/conf.d/auth.conf
auth_basic "Restricted Access";
auth_basic_user_file /etc/nginx/.htpasswd;
EOL

echo "Basic Auth setup complete."

# Launch the main process
exec "$@"
