FROM node:20-alpine

WORKDIR /app

# Copy package files first for better layer caching
COPY package.json package-lock.json* ./
RUN npm install

# Source is mounted via docker-compose volume for hot reload
COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]
