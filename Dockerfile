# OmniRoute on Render: installs the prebuilt npm package instead of
# compiling from source (the source build needs over 8 GB of memory).
FROM node:24-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 make g++ ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ARG OMNIROUTE_VERSION=latest
RUN npm install -g omniroute@${OMNIROUTE_VERSION} && npm cache clean --force

ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    HOST=0.0.0.0 \
    OMNIROUTE_SERVER_HOST=0.0.0.0 \
    DATA_DIR=/app/data \
    PORT=10000

RUN mkdir -p /app/data
WORKDIR /app
EXPOSE 10000

CMD ["sh", "-c", "omniroute --port ${PORT}"]
