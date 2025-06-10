# Use a recent, stable Python slim image based on Debian Bullseye
FROM python:3.10-slim-bullseye

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file first to leverage Docker layer caching
COPY requirements.txt .

# Install Python packages from requirements.txt
# --no-cache-dir reduces image size
RUN pip install --no-cache-dir -r requirements.txt

# --- Playwright-Specific Setup ---
# Playwright provides a command to install all necessary OS-level dependencies for its browsers.
# This is much cleaner than listing dozens of 'apt-get install' packages manually.
# We specify 'chromium' to only install dependencies for that browser.
RUN playwright install-deps chromium

# Now, install the actual Chromium browser binary managed by Playwright
RUN playwright install chromium
# --- End of Playwright-Specific Setup ---

# Copy the rest of your application code into the container
COPY mmOIShijacker.py .

# Set the command to run your script when the container starts
CMD ["python", "mmOIShijacker.py"]
