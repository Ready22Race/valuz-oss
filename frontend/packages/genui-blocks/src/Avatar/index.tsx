"use client";

import { defineComponent } from "@openuidev/react-lang";

// The one URL allow-list in this package. Anything that is not an absolute
// http(s) URL returns `undefined`, so a `javascript:` or `data:text/html`
// string the model picked up from a fetched page never reaches `src`.
// Imported rather than reimplemented: a second copy is a second thing to
// forget to harden.
import { safeHref } from "../Citation/safe-href";
import { readTextFromKeys } from "../lib/props";
import { initialsOf } from "./initials";
import { AvatarSchema } from "./schema";

export { AvatarSchema } from "./schema";

export const Avatar = defineComponent({
  name: "Avatar",
  props: AvatarSchema,
  description:
    "A round portrait for a person or organisation: the image when there is one, the initials of name when there is not. Use it beside a name in a byline, a quote attribution, or a row of contributors. " +
    "name is required and is what the initials are derived from, so never leave it out to show only a picture. imageUrl must be an http or https address — any other scheme, or a relative path, is ignored and the initials are drawn instead, so do not invent a URL just to fill the circle. " +
    "size is small|medium|large (medium by default). For a name with a role and a detail line beneath it, use ProfileTile, which draws this circle itself.",
  component: ({ props }) => {
    const record = props as unknown as Record<string, unknown>;
    const name = readTextFromKeys(record, ["name", "title", "label"]);
    const src = safeHref(record.imageUrl ?? record.avatarUrl ?? record.image);
    const size = props.size ?? "medium";

    if (src) {
      /*
       * `alt` carries the name rather than being empty: unlike a favicon in a
       * source row, this block can stand on its own beside a byline, and there
       * is no guarantee the name is repeated in text next to it.
       * `no-referrer` stops the host's URL leaking to the image's origin.
       */
      return (
        <img
          className="vgb-avatar"
          data-slot="vgb-avatar"
          data-size={size}
          src={src}
          alt={name}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
        />
      );
    }

    return (
      <span className="vgb-avatar" data-slot="vgb-avatar" data-size={size} title={name}>
        <span aria-hidden="true">{initialsOf(name)}</span>
        {/* The initials are a drawing of the name; the name itself is what a
            screen reader should hear, and only when it is not already beside
            the circle in text — which is why it is the element's own content
            rather than an `aria-label` on a decorative span. */}
        <span className="vgb-avatar-sr">{name}</span>
      </span>
    );
  },
});
