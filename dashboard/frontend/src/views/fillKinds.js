// Kinds whose renderer sizes itself to the frame it is given (canvas/iframe/
// graph roots that use h-full) rather than to its content. They only fill when
// the wrapper is a definite-height flex column: content kinds must NOT get one,
// or a long table/document would be clamped to the frame and cut off.
// Its own module so ViewRenderer stays a component-only module (fast refresh).
export const FILL_KINDS = new Set(['html', 'scene3d', 'graph', 'simulation', 'slides', 'process', 'math', 'chart']);
