import React, { Suspense, useRef, useEffect, useMemo } from 'react';
import { Canvas, useThree } from '@react-three/fiber';
import { OrbitControls, useGLTF, useAnimations, ContactShadows, Bounds } from '@react-three/drei';
import { clone as cloneGltfScene } from 'three/examples/jsm/utils/SkeletonUtils.js';
import * as THREE from 'three';
import { viewAssetUrl } from '../../api';
import { useI18n } from '../../i18n';

// 3D scene renderer — three.js via react-three-fiber.
//
// This renderer **shows** geometry; it does not build any. Meshes are authored
// by the Blender geometry engine (connectors/blender) and arrive as glTF assets,
// so a scene object is a loaded model plus a transform, and the work here is
// what three.js is actually good at: loading, lighting, framing and letting a
// model's parts be addressed by name.
//
// A loaded glTF model stays addressable: `obj.nodes` / `obj.materials` /
// `obj.morphs` / `obj.animation` override its parts *by name*, applied over a
// snapshot of the model's own values so removing an override restores the
// original.

function resolveUrl(view, ref) {
  if (!ref || typeof ref !== 'string') return null;
  if (ref.startsWith('asset://')) return view?.view_id ? viewAssetUrl(view.view_id, ref.slice(8)) : null;
  return ref;
}

function asList(coll) {
  if (Array.isArray(coll)) return coll.map((el, i) => [el?.id ?? String(i), el]);
  if (coll && typeof coll === 'object') return Object.entries(coll);
  return [];
}

function normScale(scale) {
  if (scale == null) return [1, 1, 1];
  if (typeof scale === 'number') return [scale, scale, scale];
  return scale;
}

// ── model part overrides ─────────────────────────────────────────────────────

// GLTFLoader names a multi-primitive mesh's children `<node>_0`, `<node>_1`, so
// an override keyed by the node name has to reach those too. '*' hits everything.
function nameMatches(name, key) {
  if (key === '*') return true;
  if (!name) return false;
  return name === key || name.startsWith(`${key}_`);
}

function materialsOf(obj) {
  if (!obj?.isMesh || !obj.material) return [];
  return Array.isArray(obj.material) ? obj.material : [obj.material];
}

const MATERIAL_NUMBERS = ['metalness', 'roughness', 'opacity', 'emissiveIntensity',
  'reflectivity', 'clearcoat', 'clearcoatRoughness', 'iridescence', 'transmission'];
const MATERIAL_FLAGS = ['wireframe', 'visible', 'flatShading', 'transparent', 'depthWrite'];

function snapshotMaterial(m) {
  const snap = { color: m.color?.getHex?.(), emissive: m.emissive?.getHex?.() };
  for (const k of MATERIAL_NUMBERS) if (k in m) snap[k] = m[k];
  for (const k of MATERIAL_FLAGS) if (k in m) snap[k] = m[k];
  return snap;
}

function restoreMaterial(m, snap) {
  if (!snap) return;
  if (snap.color !== undefined && m.color) m.color.setHex(snap.color);
  if (snap.emissive !== undefined && m.emissive) m.emissive.setHex(snap.emissive);
  for (const k of [...MATERIAL_NUMBERS, ...MATERIAL_FLAGS]) if (k in snap) m[k] = snap[k];
  m.needsUpdate = true;
}

function patchMaterial(m, patch) {
  if (!patch || typeof patch !== 'object') return;
  if (patch.color !== undefined && m.color) m.color.set(patch.color);
  if (patch.emissive !== undefined && m.emissive) m.emissive.set(patch.emissive);
  for (const k of MATERIAL_NUMBERS) if (patch[k] !== undefined) m[k] = Number(patch[k]);
  for (const k of MATERIAL_FLAGS) if (patch[k] !== undefined) m[k] = !!patch[k];
  // an opacity override implies transparency unless the spec says otherwise
  if (patch.opacity !== undefined && patch.transparent === undefined) m.transparent = Number(patch.opacity) < 1;
  m.needsUpdate = true;
}

function applyMorphs(node, morphs) {
  if (!node?.morphTargetDictionary || !node.morphTargetInfluences) return;
  for (const [target, weight] of Object.entries(morphs || {})) {
    const idx = node.morphTargetDictionary[target] ?? (Number.isInteger(+target) ? +target : undefined);
    if (idx !== undefined && idx in node.morphTargetInfluences) {
      node.morphTargetInfluences[idx] = Number(weight) || 0;
    }
  }
}

function patchNode(node, patch) {
  if (!patch || typeof patch !== 'object') return;
  if (patch.visible !== undefined) node.visible = !!patch.visible;
  if (Array.isArray(patch.position)) node.position.fromArray(patch.position);
  if (Array.isArray(patch.rotation)) node.rotation.set(...patch.rotation);
  if (patch.scale !== undefined) node.scale.fromArray(normScale(patch.scale));
  if (patch.material) for (const m of materialsOf(node)) patchMaterial(m, patch.material);
  if (patch.morphs) applyMorphs(node, patch.morphs);
}

function Model({ view, obj }) {
  const url = resolveUrl(view, obj?.src);
  const { scene, animations } = useGLTF(url);
  const group = useRef();

  // One deep, skinning-safe clone per scene object, with its own material
  // instances — so overrides never leak into the loader's cache or into a
  // second copy of the same .glb.
  const root = useMemo(() => {
    const copy = cloneGltfScene(scene);
    copy.traverse((o) => {
      if (!o.isMesh) return;
      o.material = Array.isArray(o.material) ? o.material.map((m) => m.clone()) : o.material.clone();
      o.castShadow = true;
      o.receiveShadow = true;
    });
    return copy;
  }, [scene]);

  const defaults = useMemo(() => {
    const nodes = new Map();
    const mats = new Map();
    root.traverse((o) => {
      nodes.set(o.uuid, {
        visible: o.visible,
        position: o.position.toArray(),
        rotation: [o.rotation.x, o.rotation.y, o.rotation.z],
        scale: o.scale.toArray(),
        morphs: o.morphTargetInfluences ? [...o.morphTargetInfluences] : null,
      });
      for (const m of materialsOf(o)) if (!mats.has(m.uuid)) mats.set(m.uuid, snapshotMaterial(m));
    });
    return { nodes, mats };
  }, [root]);

  // Re-derive the whole override state on every spec change: reset to the
  // captured original first, then re-apply. That makes removing an override a
  // real undo, and keeps the result independent of op order.
  const nodeOverrides = obj?.nodes;
  const materialOverrides = obj?.materials;
  const morphOverrides = obj?.morphs;
  const overrideKey = useMemo(
    () => JSON.stringify([nodeOverrides ?? null, materialOverrides ?? null, morphOverrides ?? null]),
    [nodeOverrides, materialOverrides, morphOverrides],
  );

  useEffect(() => {
    root.traverse((o) => {
      const d = defaults.nodes.get(o.uuid);
      if (d) {
        o.visible = d.visible;
        o.position.fromArray(d.position);
        o.rotation.set(...d.rotation);
        o.scale.fromArray(d.scale);
        if (d.morphs && o.morphTargetInfluences) {
          for (let i = 0; i < d.morphs.length; i += 1) o.morphTargetInfluences[i] = d.morphs[i];
        }
      }
      for (const m of materialsOf(o)) restoreMaterial(m, defaults.mats.get(m.uuid));
    });

    for (const [key, patch] of Object.entries(nodeOverrides || {})) {
      root.traverse((o) => { if (nameMatches(o.name, key)) patchNode(o, patch); });
    }
    for (const [key, patch] of Object.entries(materialOverrides || {})) {
      root.traverse((o) => {
        for (const m of materialsOf(o)) if (nameMatches(m.name, key)) patchMaterial(m, patch);
      });
    }
    if (morphOverrides) root.traverse((o) => applyMorphs(o, morphOverrides));
  }, [root, defaults, overrideKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Baked animation clips: the agent names one and it plays; the user can stop
  // it or scrub speed through a control bound to spec.objects.<id>.animation.
  const { actions } = useAnimations(animations, group);
  const anim = obj?.animation;
  const animKey = useMemo(() => JSON.stringify(anim ?? null), [anim]);
  useEffect(() => {
    if (!actions) return undefined;
    const action = anim?.clip ? actions[anim.clip] : null;
    if (!action) {
      Object.values(actions).forEach((a) => a?.stop());
      return undefined;
    }
    action.reset();
    action.setEffectiveTimeScale(anim.speed ?? 1);
    // eslint-disable-next-line react-hooks/immutability -- three.js drives playback by mutating the action
    action.paused = anim.playing === false;
    action.play();
    if (typeof anim.time === 'number') action.time = anim.time;
    return () => { action.stop(); };
  }, [actions, animKey]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <group ref={group} position={obj.position || [0, 0, 0]}
      rotation={obj.rotation || [0, 0, 0]} scale={normScale(obj.scale)}
      visible={obj.visible !== false}>
      <primitive object={root} />
    </group>
  );
}

// ── objects ──────────────────────────────────────────────────────────────────

function SceneObject({ view, obj }) {
  if (!obj) return null;
  if (!obj.src) {
    // Not a hole in the scene by accident: geometry comes from the engine as an
    // asset, so an object without one has nothing to show yet.
    console.warn('[SceneView] object has no model src, nothing to render', obj.id);
    return null;
  }
  return (
    <Suspense fallback={null}>
      <Model view={view} obj={obj} />
    </Suspense>
  );
}

// ── scene ────────────────────────────────────────────────────────────────────

function Lights({ lights }) {
  const list = asList(lights);
  if (!list.length) {
    return (<>
      <ambientLight intensity={0.6} />
      <directionalLight position={[5, 10, 7]} intensity={1.1} castShadow />
    </>);
  }
  return list.map(([id, l]) => {
    const color = l?.color || '#ffffff';
    const intensity = l?.intensity ?? 1;
    const pos = l?.position || [5, 10, 5];
    switch (l?.type) {
      case 'ambient': return <ambientLight key={id} color={color} intensity={intensity} />;
      case 'point': return <pointLight key={id} color={color} intensity={intensity} position={pos} />;
      case 'hemisphere': return <hemisphereLight key={id} color={color} intensity={intensity} />;
      case 'directional':
      default: return <directionalLight key={id} color={color} intensity={intensity} position={pos} castShadow />;
    }
  });
}

function CameraRig({ camera, controlsRef }) {
  const { camera: cam } = useThree();
  useEffect(() => {
    if (camera?.position && Array.isArray(camera.position)) cam.position.set(...camera.position);
    // eslint-disable-next-line react-hooks/immutability -- the three.js camera is mutated in place
    if (camera?.fov) { cam.fov = camera.fov; cam.updateProjectionMatrix(); }
    if (controlsRef.current && camera?.target) {
      controlsRef.current.target.set(...camera.target);
      controlsRef.current.update();
    }
  }, [camera, cam, controlsRef]);
  return null;
}

class ErrorBoundary extends React.Component {
  constructor(p) { super(p); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }

  // A three.js error reads as a stray TypeError from inside a constructor, so
  // the message alone is untraceable. The console gets the React stack (which
  // names the failing object's component) plus whatever the caller can say
  // about *where* in the spec this boundary sits.
  componentDidCatch(err, info) {
    console.error(`[SceneView] ${this.props.scope || 'scene'} failed`, err, info?.componentStack);
  }

  render() {
    if (this.state.err) {
      return this.props.quiet ? null : (
        <div className="text-sm text-red-600 p-4">
          {this.props.label}: {String(this.state.err.message || this.state.err)}
          {this.props.scope ? <span className="text-red-400"> ({this.props.scope})</span> : null}
        </div>
      );
    }
    return this.props.children;
  }
}

export default function SceneView({ view, theme }) {
  const { t } = useI18n();
  const controlsRef = useRef();
  const spec = view?.spec || {};
  // One boundary per object: "the scene is broken" is a useless report, so the
  // console names the object that threw and the others still render. Quiet,
  // since a DOM error card cannot be drawn inside a WebGL canvas anyway.
  const content = asList(spec.objects).map(([id, obj]) => (
    <ErrorBoundary key={id} scope={`object "${id}"`} label={t('viewSceneView.sceneError')} quiet>
      <SceneObject view={view} obj={obj} />
    </ErrorBoundary>
  ));

  const camera = spec.camera || {};
  const env = spec.environment || {};
  const bg = env.background || (theme === 'dark' ? '#0b1120' : '#f1f5f9');

  return (
    <ErrorBoundary label={t('viewSceneView.sceneError')} scope="scene">
      {/* the absolute inner box gives the canvas a definite size even where the
          card body's height is auto (h-full/100% would collapse there) */}
      <div className="relative w-full h-full min-h-[360px]">
        <div className="absolute inset-0">
        <Canvas shadows camera={{ position: camera.position || [4, 4, 6], fov: camera.fov || 50 }} style={{ background: bg }}>
          <CameraRig camera={camera} controlsRef={controlsRef} />
          <Lights lights={spec.lights} />
          {env.grid === false ? null : <gridHelper args={[20, 20, '#64748b', '#334155']} />}
          {/* `fit` frames whatever is in the scene — the sane default for a
              model or a generated body whose real scale nobody knows yet.
              Framing is a *cut*, not a camera move: Bounds otherwise lerps the
              camera to the fit over its default 1s, which reads as an unasked-
              for fly-in every time the view is opened or reloaded. A maxDuration
              this small makes the first animated frame satisfy t >= 1, so Bounds
              snaps straight to the goal and hands the camera back. `observe` is
              off for the same reason — with it the flight re-ran on every canvas
              resize (opening the card, toggling the sidebar, resizing the
              window), stomping wherever the user had orbited to. */}
          {env.fit
            ? <Bounds fit clip margin={env.margin ?? 1.2} maxDuration={0.001}>{content}</Bounds>
            : content}
          {env.shadows && (
            <ContactShadows position={[0, env.floor ?? 0, 0]} opacity={env.shadowOpacity ?? 0.45}
              scale={env.shadowScale ?? 20} blur={2.4} far={10} />
          )}
          <OrbitControls ref={controlsRef} makeDefault
            autoRotate={!!env.autoRotate} autoRotateSpeed={env.autoRotateSpeed ?? 1} />
        </Canvas>
        </div>
      </div>
    </ErrorBoundary>
  );
}
