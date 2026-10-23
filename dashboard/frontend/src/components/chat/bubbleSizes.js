// The row and bubble classes of a chat message, at full size (the Chat page, a
// page-wide conversation) and compact (a chat in a side column, where an avatar
// gutter and a 72% bubble would leave the text a third of the column).
// Shared by MessageBubble and BuildMessage so the two views keep one shape.
export const BUBBLE_SIZES = {
  full: {
    row: 'flex gap-3 mb-6 mx-2',
    bubble: 'max-w-[88%] sm:max-w-[72%] text-base px-4 py-3 rounded-2xl',
  },
  compact: {
    row: 'flex gap-2 mb-3',
    bubble: 'max-w-[94%] text-sm px-3 py-2 rounded-xl',
  },
};
