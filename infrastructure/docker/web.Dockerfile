FROM node:22-alpine AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
# Rewrites are compiled at build time: the API's internal URL must be known here.
ARG JOLTIQ_API_URL=http://api:8000
ARG JOLTIQ_REMOTE_ACCESS_FRAME_ORIGIN=
ENV NEXT_TELEMETRY_DISABLED=1 JOLTIQ_API_URL=$JOLTIQ_API_URL JOLTIQ_REMOTE_ACCESS_FRAME_ORIGIN=$JOLTIQ_REMOTE_ACCESS_FRAME_ORIGIN
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
