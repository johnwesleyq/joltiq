from . import auth, orgs, tickets

ALL_ROUTERS = [auth.router, orgs.router, tickets.router]
