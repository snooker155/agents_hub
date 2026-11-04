import { createContext } from 'react';

// The phone's top bar (Layout.jsx) holds the page's title in place of the
// service's mark and name: the element PageHeader and AppBar draw it into,
// null when there is no such bar (a wide screen, a page outside the Layout).
export const PhoneTitleSlot = createContext(null);
