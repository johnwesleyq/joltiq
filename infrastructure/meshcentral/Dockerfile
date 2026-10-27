FROM node:22-alpine

RUN apk add --no-cache su-exec \
    && npm install --omit=dev --prefix /opt/meshcentral meshcentral@1.3.1 \
    && chown -R node:node /opt/meshcentral

COPY --chown=root:root infrastructure/meshcentral/patch-direct-consent.js /tmp/patch-direct-consent.js
RUN node /tmp/patch-direct-consent.js && rm /tmp/patch-direct-consent.js

WORKDIR /opt/meshcentral
COPY --chown=root:root infrastructure/meshcentral/entrypoint.sh /usr/local/bin/joltiq-meshcentral-entrypoint
COPY --chown=root:root infrastructure/meshcentral/config.json /opt/meshcentral/config-template.json
RUN chmod 755 /usr/local/bin/joltiq-meshcentral-entrypoint
EXPOSE 443
ENTRYPOINT ["/usr/local/bin/joltiq-meshcentral-entrypoint"]
CMD ["node", "node_modules/meshcentral/meshcentral.js"]
