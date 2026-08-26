import fixture from "./preview_fixture.json" with { type: "json" };

Deno.test("preview fixture covers full public post fields", () => {
  if (!fixture.id || !fixture.title || !fixture.desc || !fixture.timestamp) throw new Error("missing core post fields");
  if (!fixture.user?.nickname) throw new Error("missing public author display field");
  if (!Array.isArray(fixture.images_list) || !fixture.images_list.length) throw new Error("missing image sample");
  if (typeof fixture.liked_count !== "number" || typeof fixture.comments_count !== "number") throw new Error("missing engagement metrics");
});
