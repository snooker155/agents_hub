// The row and bubble classes of a chat message, at full size (the Chat page, a
// page-wide conversation) and compact (a chat in a side column, where an avatar
// gutter and a 72% bubble would leave the text a third of the column).
// Shared by MessageBubble and BuildMessage so the two views keep one shape.
export const BUBBLE_SIZES = {
  // `footer`: the files field at the foot of a bubble, edge to edge under a
  // dividing line (the negative margins undo the bubble's own padding).
  full: {
    row: 'flex gap-3 mb-6 mx-2',
    bubble: 'max-w-[88%] sm:max-w-[72%] text-base px-4 py-3 rounded-2xl',
    footer: '-mx-4 -mb-3 mt-3 px-4 pt-2 pb-3',
  },
  compact: {
    row: 'flex gap-2 mb-3',
    bubble: 'max-w-[94%] text-sm px-3 py-2 rounded-xl',
    footer: '-mx-3 -mb-2 mt-2 px-3 pt-1.5 pb-2',
  },
};

// The name under an answer's avatar: wrapped to the avatar column's width,
// a word longer than the column broken inside it, three lines at most (the
// whole name on hover).
export const AVATAR_NAME_CLS = 'w-full text-[9px] text-gray-400 font-medium text-center leading-tight break-words [overflow-wrap:anywhere] line-clamp-3';
