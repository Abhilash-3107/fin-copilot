/**
 * The release a version belongs to: 0.3.0rc1, 0.3.0.dev2 and 0.3.0.post1 are
 * all 0.3.0. expected-changes.json describes a release, and a release candidate
 * of it has to be able to run the same journey.
 */
export function baseVersion(version) {
  const match = /^\d+(?:\.\d+)*/.exec(version)
  if (!match) throw new Error(`Not a version: ${JSON.stringify(version)}`)
  return match[0]
}
