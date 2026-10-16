/** Run in a real browser with the app's sanitizeSvg import. No network or mic needed. */
export function runSvgBrowserCases(sanitizeSvg) {
  const cases = [
    { name: 'safe paths and inline fill survive', input: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path style="fill:#abc;stroke:#123" d="M0 0L20 20"/></svg>', accept: true },
    { name: 'local gradient reference survives', input: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><defs><linearGradient id="g"><stop offset="0" stop-color="red"/></linearGradient></defs><rect width="20" height="20" fill="url(#g)"/></svg>', accept: true },
    { name: 'remote CSS URL rejected', input: '<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(https://example.com/x.svg)"/></svg>', accept: false },
    { name: 'CSS URL escape rejected', input: '<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:u\\72l(https://example.com/x.svg)"/></svg>', accept: false },
    { name: 'presentation fill CSS escape rejected', input: '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="u\\72l(https://example.invalid/x.svg)"/></svg>', accept: false },
    { name: 'presentation stroke numeric-entity CSS escape rejected', input: '<svg xmlns="http://www.w3.org/2000/svg"><rect stroke="u&#92;72l(https://example.invalid/x.svg)"/></svg>', accept: false },
    { name: 'presentation filter hex-entity CSS escape rejected', input: '<svg xmlns="http://www.w3.org/2000/svg"><rect filter="&#x5c;75rl(https://example.invalid/x.svg)"/></svg>', accept: false },
    { name: 'namespace script rejected', input: '<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="http://www.w3.org/1999/xhtml"><x:script>alert(1)</x:script></svg>', accept: false },
    { name: 'malformed XML rejected', input: '<svg xmlns="http://www.w3.org/2000/svg"><g></svg>', accept: false },
    { name: 'negative viewBox dimensions rejected', input: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 -20 20"/>', accept: false },
    { name: 'unknown active link element rejected', input: '<svg xmlns="http://www.w3.org/2000/svg"><a href="#x"><rect/></a></svg>', accept: false },
    { name: 'foreignObject rejected', input: '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>', accept: false },
    { name: 'event handler rejected', input: '<svg xmlns="http://www.w3.org/2000/svg"><rect onmouseover="alert(1)"/></svg>', accept: false },
  ];
  return cases.map(item => { let accepted = false; try { const output = sanitizeSvg(item.input); accepted = output.includes('<svg'); } catch {} return { name: item.name, passed: accepted === item.accept }; });
}
