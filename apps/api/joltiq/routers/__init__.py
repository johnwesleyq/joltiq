from . import agents, auth, devices, knowledge, orgs, sessions, tickets

ALL_ROUTERS = [auth.router, orgs.router, tickets.router, knowledge.router, agents.router, sessions.router, devices.router,
               devices.agent_router]
