import { HashRouter as RRHashRouter, MemoryRouter as RRMemoryRouter } from 'react-router-dom'
import { ROUTER_FUTURE } from '../lib/routerFuture.js'

// The routers tests render in. They carry the same future flags as the app's
// router; a test that imports a router straight from react-router-dom is
// rejected by lint.
export function MemoryRouter(props) {
  return <RRMemoryRouter future={ROUTER_FUTURE} {...props} />
}

export function HashRouter(props) {
  return <RRHashRouter future={ROUTER_FUTURE} {...props} />
}
