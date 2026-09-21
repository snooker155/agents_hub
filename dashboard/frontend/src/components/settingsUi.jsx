// The two pieces the Settings page and the connector pages both draw with.
//
// They used to live inside Settings.jsx, which was fine while the connectors
// lived there too. Now that a connector is its own page, a shared card and a
// shared input class have to be somewhere neither page owns, or one page ends
// up importing from the other.

export const inputCls =
  'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none';

export function SectionCard({ title, actions, children }) {
  return (
    <section className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 space-y-5">
      {/* The title row carries the card's own controls (a provider's status
          badge and its Test button). They used to be the first child of the
          body, which put them on a line of their own under the heading. */}
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <h2 className="text-base font-semibold text-gray-800">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}
