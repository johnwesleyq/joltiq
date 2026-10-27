const fs = require("fs");

const target = "/opt/meshcentral/node_modules/meshcentral/meshdesktopmultiplex.js";
let source = fs.readFileSync(target, "utf8");

if (source.includes("function applyConsentMessageOptions(command)")) {
  process.exit(0);
}

const sendAgentMessageHook = "    obj.sendAgentMessage = function (command, userid, domainid) {";
const helper = `    // JoltIQ: MeshCentral 1.3.1 only attaches consent message options to
    // guest-share desktop sessions. Attach the same options to ordinary direct
    // technician sessions so the Windows agent can use its reliable native
    // Allow/Deny dialog instead of silently creating only the privacy bar.
    function applyConsentMessageOptions(command) {
        if (typeof domain.consentmessages != 'object') return;
        if (typeof command.soptions != 'object') command.soptions = {};
        if (typeof domain.consentmessages.title == 'string') { command.soptions.consentTitle = domain.consentmessages.title; }
        if (typeof domain.consentmessages.desktop == 'string') { command.soptions.consentMsgDesktop = domain.consentmessages.desktop; }
        if (typeof domain.consentmessages.terminal == 'string') { command.soptions.consentMsgTerminal = domain.consentmessages.terminal; }
        if (typeof domain.consentmessages.files == 'string') { command.soptions.consentMsgFiles = domain.consentmessages.files; }
        if ((typeof domain.consentmessages.consenttimeout == 'number') && (domain.consentmessages.consenttimeout > 0)) { command.soptions.consentTimeout = domain.consentmessages.consenttimeout; }
        if (domain.consentmessages.autoacceptontimeout === true) { command.soptions.consentAutoAccept = true; }
        if (domain.consentmessages.autoacceptifnouser === true) { command.soptions.consentAutoAcceptIfNoUser = true; }
        if (domain.consentmessages.autoacceptiflocked === true) { command.soptions.consentAutoAcceptIfLocked = true; }
        if (domain.consentmessages.oldstyle === true) { command.soptions.oldStyle = true; }
    }

`;

if (!source.includes(sendAgentMessageHook)) {
  throw new Error("MeshCentral direct-session consent hook was not found");
}
source = source.replace(sendAgentMessageHook, helper + sendAgentMessageHook);

const privacyBarHook = "                    if (typeof domain.desktopprivacybartext == 'string') { command.privacybartext = domain.desktopprivacybartext; } // Privacy bar text";
const occurrences = source.split(privacyBarHook).length - 1;
if (occurrences !== 2) {
  throw new Error(`Expected two direct-session send paths, found ${occurrences}`);
}
source = source.replaceAll(
  privacyBarHook,
  "                    applyConsentMessageOptions(command);\n" + privacyBarHook,
);

fs.writeFileSync(target, source);
