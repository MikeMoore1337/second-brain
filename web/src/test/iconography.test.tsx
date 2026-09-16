import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ICON_NAMES, Icon } from "../icons";

describe("local icon system", () => {
  it("ships every semantic icon as lazy local raster art without an external loader", () => {
    expect(ICON_NAMES).toContain("decision");
    expect(ICON_NAMES).toContain("growth");
    expect(ICON_NAMES).toContain("self-retrieval");
    expect(ICON_NAMES).toContain("relation");
    expect(ICON_NAMES).toContain("personal-planning");
    expect(ICON_NAMES).toContain("adaptive-profile");
    for (const name of ICON_NAMES) {
      const markup = renderToStaticMarkup(<Icon name={name} />);
      expect(markup).toContain('<img');
      expect(markup).toContain('.webp');
      expect(markup).toContain('loading="lazy"');
      expect(markup).toContain('alt=""');
      expect(markup).toContain('aria-hidden="true"');
      expect(markup).not.toMatch(/https?:|data:image|<svg/);
    }
    expect(renderToStaticMarkup(<Icon name="search" size={20} />)).toContain('search-compact');
    expect(renderToStaticMarkup(<Icon name="search" size={64} />)).toContain('search-detail');
    expect(renderToStaticMarkup(<Icon name="adaptive-profile" size={20} />)).toContain('adaptive-profile-compact');
    expect(renderToStaticMarkup(<Icon name="adaptive-profile" size={64} />)).toContain('adaptive-profile-detail');
  });

  it("gives a meaningful accessible name when an icon is standalone", () => {
    const markup = renderToStaticMarkup(<Icon name="close" label="Закрыть" />);
    expect(markup).toContain('role="img"');
    expect(markup).toContain('aria-label="Закрыть"');
    expect(markup).toContain('alt="Закрыть"');
    expect(markup).not.toContain('aria-hidden="true"');
  });
});
