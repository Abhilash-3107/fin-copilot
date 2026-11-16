// The React Router v7 flags, set on the app's router and on every router the
// tests build, so the two cannot drift. React Router warns only about a flag
// that is left undefined, so each one is set on purpose:
//
// - v7_startTransition: false keeps what the app has always done, applying a
//   navigation at once. Turning it on would hold the page you left on screen
//   until the next one has loaded, which this app's pages are not built for.
// - v7_relativeSplatPath: true takes the v7 way of resolving relative links
//   inside a splat route. No route here is affected, so it changes nothing today.
export const ROUTER_FUTURE = {
  v7_startTransition: false,
  v7_relativeSplatPath: true,
}
