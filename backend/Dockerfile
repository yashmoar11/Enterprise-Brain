FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if needed (e.g. for potential neo4j driver build deps, though usually not needed for pure python driver)
# RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir langchain-neo4j==0.8.0 && \
    pip install --no-cache-dir langgraph==1.1.3

COPY . .

# Expose the port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
