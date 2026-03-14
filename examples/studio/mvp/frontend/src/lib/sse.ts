export type ParsedSseEvent = {
  id?: string;
  event: string;
  data: string;
};

export function parseSseText(text: string): ParsedSseEvent[] {
  const normalized = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const lines = normalized.split('\n');

  const events: ParsedSseEvent[] = [];

  let curId: string | undefined;
  let curEvent: string | undefined;
  let curDataLines: string[] = [];

  const flush = () => {
    if (curEvent === undefined && curDataLines.length === 0 && curId === undefined) return;
    if (curEvent === undefined) {
      // Spec default is "message", but our server always sends explicit `event:`.
      curEvent = 'message';
    }
    events.push({
      id: curId,
      event: curEvent,
      data: curDataLines.join('\n'),
    });
    curId = undefined;
    curEvent = undefined;
    curDataLines = [];
  };

  for (const rawLine of lines) {
    const line = rawLine;
    if (line === '') {
      flush();
      continue;
    }
    if (line.startsWith(':')) continue; // comment / keep-alive

    const idx = line.indexOf(':');
    const field = idx === -1 ? line : line.slice(0, idx);
    let value = idx === -1 ? '' : line.slice(idx + 1);
    if (value.startsWith(' ')) value = value.slice(1);

    switch (field) {
      case 'id':
        curId = value;
        break;
      case 'event':
        curEvent = value;
        break;
      case 'data':
        curDataLines.push(value);
        break;
      default:
        break;
    }
  }

  flush();
  return events;
}
