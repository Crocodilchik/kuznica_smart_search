FROM node:24-alpine AS base
WORKDIR /app/frontend
ENV NEXT_TELEMETRY_DISABLED=1

FROM base AS dependencies
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM base AS builder
ENV NODE_ENV=production
COPY --from=dependencies /app/frontend/node_modules ./node_modules
COPY frontend/ ./
COPY smart_search/interfaces/ui_config.json /app/smart_search/interfaces/ui_config.json
RUN npm run build

FROM base AS runner
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000
RUN addgroup -S -g 10001 nodeapp && adduser -S -u 10001 -G nodeapp nodeapp
COPY --from=builder --chown=nodeapp:nodeapp /app/frontend/package.json ./package.json
COPY --from=builder --chown=nodeapp:nodeapp /app/frontend/node_modules ./node_modules
COPY --from=builder --chown=nodeapp:nodeapp /app/frontend/.next ./.next
COPY --from=builder --chown=nodeapp:nodeapp /app/frontend/public ./public
COPY --from=builder --chown=nodeapp:nodeapp /app/frontend/src ./src
COPY --from=builder --chown=nodeapp:nodeapp /app/frontend/next.config.ts ./next.config.ts
COPY --from=builder --chown=nodeapp:nodeapp /app/smart_search/interfaces/ui_config.json /app/smart_search/interfaces/ui_config.json
USER nodeapp
EXPOSE 3000
CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0"]
