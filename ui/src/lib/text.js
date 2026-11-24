// The API counts a name or note in characters (code points), so the page must
// too: an input's maxLength counts UTF-16 units and would cut an emoji note at
// half the length the API allows.
export const capCharacters = (text, max) => {
  const chars = Array.from(text)
  return chars.length > max ? chars.slice(0, max).join('') : text
}
