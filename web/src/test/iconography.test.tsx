import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ICON_NAMES, Icon } from "../icons";

describe("local icon system", () => {
  it("ships the bounded semantic mapping as local currentColor SVG", () => {
    expect(ICON_NAMES).toContain("decision");
    expect(ICON_NAMES).toContain("growth");
    expect(ICON_NAMES).toContain("self-retrieval");
    expect(ICON_NAMES).toContain("relation");
    const markup = renderToStaticMarkup(<Icon name="search" />);
    expect(markup).toContain('data-icon="search"');
    expect(markup).toContain('stroke="currentColor"');
    expect(markup).toContain('stroke-width="1.5"');
    expect(markup).toContain('aria-hidden="true"');
  });

  it("gives a meaningful accessible name when an icon is standalone", () => {
    const markup = renderToStaticMarkup(<Icon name="close" label="Закрыть" />);
    expect(markup).toContain('role="img"');
    expect(markup).toContain('aria-label="Закрыть"');
    expect(markup).toContain("<title>Закрыть</title>");
    expect(markup).not.toContain('aria-hidden="true"');
  });
});
