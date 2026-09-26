import os

# Loaded by pytest before any test module imports forecaster: never read backend/.env in tests,
# so real API keys and the live DATABASE_URL can't be picked up.
os.environ["FORECASTER_NO_DOTENV"] = "1"
os.environ["AUTO_RETRAIN"] = "false"  # an upload in a test must never start a 6-minute retrain
