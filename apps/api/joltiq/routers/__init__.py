from . import admin, agents, auth, devices, integrations, knowledge, orgs, sessions, tickets

ALL_ROUTERS = [auth.router, orgs.router, tickets.router, knowledge.router, agents.router, sessions.router, devices.router,
               devices.agent_router, integrations.router, admin.router]
