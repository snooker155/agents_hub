import { Component, useMemo, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { Html, OrbitControls } from '@react-three/drei';
import { useI18n } from '../../i18n';
import { useThemeColors } from '../../lib/themeColors';
import { DTYPE_COLOR_SPEC, dtypeColorKey, formatBytes, formatCount, layerScale } from './graph';

// The 3D view: the same blocks as a vertical stack of slabs, embedding on top.
// Thickness follows layerScale with a floor so a norm is still a visible
// sliver; `other` blocks stand in a second, smaller stack to the side. The
// scene stays cheap: one ambient and one directional light, no shadows.

const SCENE_COLOR_SPEC = {
  ...DTYPE_COLOR_SPEC,
  outline: ['--brand-500', 'royalblue'],
};

const WIDTH = 3;
const DEPTH = 2;
const GAP = 0.08;
const MIN_THICK = 0.06;
const MAX_THICK = 0.6;
const SIDE_X = 3.6;

/** Positions for every slab: `{id: {y, thick, x}}`, stacked downwards. */
function layoutSlabs(nodes, scale) {
  const out = {};
  let mainY = 0;
  let sideY = 0;
  for (const node of nodes) {
    const thick = MIN_THICK + (MAX_THICK - MIN_THICK) * (scale[node.id] ?? 0);
    if (node.side) {
      out[node.id] = { x: SIDE_X, y: sideY - thick / 2, thick };
      sideY -= thick + GAP;
    } else {
      out[node.id] = { x: 0, y: mainY - thick / 2, thick };
      mainY -= thick + GAP;
    }
  }
  const height = Math.max(-mainY, -sideY, 1);
  // Centre the stack on the origin so the orbit pivots around its middle.
  for (const id of Object.keys(out)) out[id].y += height / 2;
  return { slabs: out, height };
}

function Slab({ node, slab, color, outline, selected, onSelect }) {
  const [hovered, setHovered] = useState(false);
  const width = node.side ? WIDTH * 0.6 : WIDTH;
  const depth = node.side ? DEPTH * 0.6 : DEPTH;
  // The selected slab slides forward a little so it reads as picked out.
  const z = selected ? 0.35 : 0;
  return (
    <group position={[slab.x, slab.y, z]}>
      <mesh
        onClick={(e) => { e.stopPropagation(); onSelect?.(node.id); }}
        onPointerOver={(e) => { e.stopPropagation(); setHovered(true); }}
        onPointerOut={() => setHovered(false)}
      >
        <boxGeometry args={[width, slab.thick, depth]} />
        <meshStandardMaterial
          color={color}
          emissive={selected || hovered ? outline : color}
          emissiveIntensity={selected ? 0.35 : hovered ? 0.2 : 0}
          roughness={0.6}
        />
      </mesh>
      {selected && (
        <mesh>
          <boxGeometry args={[width + 0.06, slab.thick + 0.06, depth + 0.06]} />
          <meshBasicMaterial color={outline} wireframe />
        </mesh>
      )}
      {hovered && (
        <Html position={[width / 2 + 0.2, 0, 0]} style={{ pointerEvents: 'none' }}>
          <div className="whitespace-nowrap rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-700 shadow">
            <div className="font-medium text-gray-900">{node.label}</div>
            <div>{formatCount(node.parameters)} · {formatBytes(node.bytes)}</div>
          </div>
        </Html>
      )}
    </group>
  );
}

class WebGLBoundary extends Component {
  constructor(props) { super(props); this.state = { failed: false }; }
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(err) { console.error('[Structure3D] WebGL canvas failed', err); }
  render() {
    if (this.state.failed) {
      return (
        <div className="flex h-full items-center justify-center p-6 text-center text-sm text-gray-500" data-testid="webgl-unavailable">
          {this.props.message}
        </div>
      );
    }
    return this.props.children;
  }
}

export default function Structure3D({ graph, selectedId, onSelect }) {
  const { t } = useI18n();
  const colors = useThemeColors(SCENE_COLOR_SPEC);
  const scale = useMemo(() => layerScale(graph.nodes), [graph]);
  const { slabs, height } = useMemo(() => layoutSlabs(graph.nodes, scale), [graph, scale]);
  const distance = Math.max(8, height * 1.1);

  return (
    <WebGLBoundary message={t('modelStructure.scene.webglUnavailable')}>
      <div className="relative h-full w-full" data-testid="structure-3d">
        <div className="absolute inset-0">
          <Canvas
            camera={{ position: [distance * 0.55, distance * 0.2, distance * 0.8], fov: 45, near: 0.1, far: distance * 10 }}
            gl={{ alpha: true, antialias: true }}
            style={{ background: 'transparent' }}
          >
            <ambientLight intensity={0.6} />
            <directionalLight position={[5, 10, 7]} intensity={0.9} />
            {graph.nodes.map((node) => (
              <Slab
                key={node.id}
                node={node}
                slab={slabs[node.id]}
                color={colors[dtypeColorKey(node.dtype)] || colors.other}
                outline={colors.outline}
                selected={node.id === selectedId}
                onSelect={onSelect}
              />
            ))}
            <OrbitControls makeDefault enableDamping={false} />
          </Canvas>
        </div>
        <div className="pointer-events-none absolute bottom-2 left-3 text-xs text-gray-400">
          {t('modelStructure.scene.hoverHint')}
        </div>
      </div>
    </WebGLBoundary>
  );
}
