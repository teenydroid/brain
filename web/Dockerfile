# syntax=docker/dockerfile:1

# The landing page: Next.js in standalone mode, served by node on :3000.

FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# The feed URL is read at request time (dynamic route), so nothing is baked in,
# except the Solana RPCs /rewards uses from the browser: Next inlines
# NEXT_PUBLIC_* at build time. Empty means the public cluster endpoints.
ARG NEXT_PUBLIC_SOLANA_RPC=""
ARG NEXT_PUBLIC_SOLANA_RPC_DEVNET=""
ENV NEXT_PUBLIC_SOLANA_RPC=$NEXT_PUBLIC_SOLANA_RPC NEXT_PUBLIC_SOLANA_RPC_DEVNET=$NEXT_PUBLIC_SOLANA_RPC_DEVNET
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 web
COPY --from=builder --chown=web:nodejs /app/.next/standalone ./
COPY --from=builder --chown=web:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=web:nodejs /app/public ./public
USER web

EXPOSE 3000
CMD ["node", "server.js"]
