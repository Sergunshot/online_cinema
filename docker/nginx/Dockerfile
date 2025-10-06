# Use the official Nginx image as a base
FROM nginx:latest

# Install the necessary packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        apache2-utils \
        dos2unix \
        bash && \
    rm -rf /var/lib/apt/lists/*

# Copy commands
COPY ./commands/set_nginx_basic_auth.sh /commands/set_nginx_basic_auth.sh

# Ensure Unix-style line endings for scripts
RUN dos2unix /commands/*.sh

# Add execute bit to commands files
RUN chmod +x /commands/*.sh

# Set the entry point to our script
ENTRYPOINT ["/commands/set_nginx_basic_auth.sh"]

# Run Nginx in foreground mode
CMD ["nginx", "-g", "daemon off;"]
