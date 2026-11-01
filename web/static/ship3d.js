// 3D 선박: 팀 LNG선 모델(models/lng_ship.glb, 구형 탱크 5개, 길이 305 m · 폭 약 42 m · 갑판 약 25 m)을 도안처럼 투명하게 깔고,
// 그 안에 선저 블록(홈)이나 기록 칸(선순환 화면)을 그린다. 데이터가 있는 블록(파란색)을 누르면 블록 화면으로 간다.
// 단위 m. x = 선미→선수, y = 위, z = 우현(+) / 좌현(-). 블록·탱크 배치는 2D 블록 분할도(_ui.html ship)와 같은 비율이다.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const L = 305, B = 45, D = 27, TD = 31, DB = 3;           // 길이, 폭, 갑판, 트렁크 갑판, 이중저 높이
const BX0 = 74, BL = 19.8;                                 // 블록 07의 선미 끝, 블록 길이 (07~16). 구형 탱크 하나 = 블록 2개
const TANKS = [[75, 112, 'No.5'], [114, 151, 'No.4'], [154, 191, 'No.3'], [194, 231, 'No.2'], [233, 270, 'No.1']];
const INK = 0x2B2F3A, BLUE = 0x1F4FFF;

const smooth = t => t * t * (3 - 2 * t);
// 갑판 높이에서의 반폭: 선미는 조금 좁고, 선수는 끝으로 갈수록 0
function hb(x) {
  if (x < 45) return B / 2 * (0.78 + 0.22 * smooth(x / 45));
  if (x < 235) return B / 2;
  return B / 2 * Math.pow(Math.cos(Math.min(1, (x - 235) / 70) * Math.PI / 2), 0.75);
}
// 선저(용골) 높이: 선미는 프로펠러 위로 올라가고, 선수 끝도 조금 들린다
function kz(x) {
  if (x < 40) return 9 * Math.pow(1 - x / 40, 2);
  if (x > 270) return 6 * Math.pow((x - 270) / 35, 2);
  return 0;
}
// 모델 선체를 잘라 잰 반폭: 이중저 높이(블록이 선체 밖으로 나가지 않는 폭) / 선측. 선수로 가면 좁아진다
const floorHalf = x => Math.max(9, 18 - Math.max(0, x - 235) * 0.23);
const sideHalf = x => 20.9 - Math.max(0, x - 245) * 0.16;
// 횡단면 둘레: 좌현 갑판 → 좌현 선측 → 빌지 → 선저 → 우현 → 우현 갑판. 점 수는 항상 같다 (z, y)
function section(x) {
  const h = Math.max(hb(x), 0.01), k = kz(x), r = Math.max(0.01, Math.min(4, h * 0.5, (D - k) * 0.5)), pts = [[-h, D], [-h, k + r]];
  for (let i = 1; i < 6; i++) { const a = Math.PI + Math.PI / 2 * i / 6; pts.push([-h + r + r * Math.cos(a), k + r + r * Math.sin(a)]); }
  pts.push([-h + r, k], [h - r, k]);
  for (let i = 1; i < 6; i++) { const a = 1.5 * Math.PI + Math.PI / 2 * i / 6; pts.push([h - r + r * Math.cos(a), k + r + r * Math.sin(a)]); }
  pts.push([h, k + r], [h, D]);
  return pts;
}

function hull() {
  const g = new THREE.Group(), xs = [];
  for (let x = 0; x <= L; x += 2.5) xs.push(x);
  const n = section(0).length, pos = [], idx = [];
  xs.forEach(x => section(x).forEach(([z, y]) => pos.push(x, y, z)));
  for (let i = 0; i < xs.length - 1; i++) for (let j = 0; j < n - 1; j++) {
    const a = i * n + j, b = a + n;
    idx.push(a, b, a + 1, a + 1, b, b + 1);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); geo.setIndex(idx); geo.computeVertexNormals();
  const skin = new THREE.MeshStandardMaterial({ color: 0xDCE3EF, transparent: true, opacity: 0.22, side: THREE.DoubleSide, depthWrite: false });
  g.add(new THREE.Mesh(geo, skin));
  // 선미 트랜섬과 갑판
  const tr = new THREE.ShapeGeometry(new THREE.Shape(section(0).map(([z, y]) => new THREE.Vector2(z, y))));
  tr.rotateY(-Math.PI / 2); g.add(new THREE.Mesh(tr, skin));
  const dp = [], di = [];
  xs.forEach(x => dp.push(x, D, -hb(x), x, D, hb(x)));
  for (let i = 0; i < xs.length - 1; i++) di.push(2 * i, 2 * i + 2, 2 * i + 1, 2 * i + 1, 2 * i + 2, 2 * i + 3);
  const deck = new THREE.BufferGeometry();
  deck.setAttribute('position', new THREE.Float32BufferAttribute(dp, 3)); deck.setIndex(di); deck.computeVertexNormals();
  g.add(new THREE.Mesh(deck, new THREE.MeshStandardMaterial({ color: 0xE6EAF2, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false })));
  // 도면 같은 선: 갑판 가장자리·빌지 선, 블록 경계마다 횡단면 선
  const line = (pts, color = INK, opacity = 0.9) => g.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity })));
  for (const s of [-1, 1]) {
    line(xs.map(x => new THREE.Vector3(x, D, s * hb(x))));
    line(xs.map(x => new THREE.Vector3(x, kz(x), s * Math.max(0, hb(x) - Math.min(4, hb(x) * 0.5)))), INK, 0.5);
  }
  for (let i = 0; i <= 10; i++) {
    const x = BX0 + i * BL;
    line(section(x).map(([z, y]) => new THREE.Vector3(x, y, z)), INK, 0.35);
  }
  line(section(0).concat([section(0)[0]]).map(([z, y]) => new THREE.Vector3(0, y, z)));
  return g;
}

function box(x0, x1, y0, y1, z0, z1, mat) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(x1 - x0, y1 - y0, z1 - z0), mat);
  m.position.set((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
  m.add(new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry), new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity: 0.45 })));
  return m;
}

function label(text, cls, x, y, z) {
  const el = document.createElement('span');
  el.className = 'l3d' + (cls ? ' ' + cls : ''); el.textContent = text;
  const o = new CSS2DObject(el); o.position.set(x, y, z); return o;
}

// 장면·카메라·조명: 홈과 선순환 화면이 같이 쓴다
function stage(el) {
  const scene = new THREE.Scene();
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); renderer.setClearColor(0xF7F8FB);
  el.append(renderer.domElement);
  const camera = new THREE.PerspectiveCamera(35, 1, 1, 3000);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.minDistance = 60; controls.maxDistance = 900; controls.maxPolarAngle = Math.PI - 0.05;
  scene.add(new THREE.HemisphereLight(0xFFFFFF, 0x8A96AA, 1.2));
  const sun = new THREE.DirectionalLight(0xFFFFFF, 0.9); sun.position.set(120, 260, -140); scene.add(sun);
  return { scene, renderer, camera, controls };
}

// LNG선 모델(GLB)을 기존 좌표계에 맞추고, 안쪽 블록이 잘 보이게 단색 + 투명 + 윤곽선(도안)으로 올린다
function loadShip(scene, line = 0.35) {  // line = 윤곽선 진하기
  const loader = new GLTFLoader();

  loader.load(
    '/static/models/lng_ship.glb',

    (gltf) => {
      const ship = gltf.scene;

      // ------------------------------------------------
      // 1. LNG GLB 좌표계 → 기존 BlastTrace 좌표계
      //
      // GLB : Z축이 선수-선미
      // 기존 코드 : X축이 선미→선수
      //
      // 거주구가 선미(x = 0) 쪽, 선수가 +x 쪽에 오도록 +90도
      // ------------------------------------------------
      ship.rotation.y = Math.PI / 2;

      ship.updateMatrixWorld(true);

      // ------------------------------------------------
      // 2. 모델 길이를 기존 L=305m에 맞춤
      // ------------------------------------------------
      let bbox = new THREE.Box3().setFromObject(ship);

      const size = bbox.getSize(
        new THREE.Vector3()
      );

      const scale = L / size.x;

      ship.scale.setScalar(scale);

      ship.updateMatrixWorld(true);

      // ------------------------------------------------
      // 3. 모델 중심을 기존 좌표계에 정렬
      //
      // X : 0 ~ 305
      // Y : 선저 = 0
      // Z : 선박 중심 = 0
      // ------------------------------------------------
      bbox = new THREE.Box3().setFromObject(ship);

      const center = bbox.getCenter(
        new THREE.Vector3()
      );

      ship.position.x += L / 2 - center.x;
      ship.position.y += -bbox.min.y;
      ship.position.z += -center.z;

      ship.updateMatrixWorld(true);

      // ------------------------------------------------
      // 4. 기존 schematic처럼 선체를 반투명하게
      //    → 내부 B07~B16 블록이 보이도록
      // ------------------------------------------------
      ship.traverse((obj) => {

        if (!obj.isMesh || !obj.material) {
          return;
        }

        // GLB material을 직접 수정하지 않고 clone
        if (Array.isArray(obj.material)) {

          obj.material = obj.material.map(
            mat => mat.clone()
          );

          obj.material.forEach((mat) => {
            mat.transparent = true;
            // 배의 투명도 조정
            mat.opacity = 0.08;
            mat.color.set(0xC4CDDC); mat.metalness = 0; mat.roughness = 1;
            mat.depthWrite = false;
            mat.needsUpdate = true;
          });

        } else {

          obj.material = obj.material.clone();

          obj.material.transparent = true;
          // 배의 투명도 조정
          obj.material.opacity = 0.08;
          obj.material.color.set(0xC4CDDC); obj.material.metalness = 0; obj.material.roughness = 1;
          obj.material.depthWrite = false;
          obj.material.needsUpdate = true;
        }

        obj.renderOrder = 1;
      });

      const meshes = [];
      ship.traverse(o => { if (o.isMesh) meshes.push(o); });
      meshes.forEach(o => o.add(new THREE.LineSegments(new THREE.EdgesGeometry(o.geometry, 40),
        new THREE.LineBasicMaterial({ color: 0x46557A, transparent: true, opacity: line }))));

      scene.add(ship);

      // 확인용
      const finalBox =
        new THREE.Box3().setFromObject(ship);

      const finalSize =
        finalBox.getSize(new THREE.Vector3());

      console.log('LNG ship loaded');
      console.log('Final ship size:', finalSize);
      console.log('Final bbox:', finalBox);
    },

    undefined,

    (error) => {
      console.error(
        'GLB load error:',
        error
      );
    }
  );
}

// 가리키면 이름(userData.title), 누르면(끌지 않았을 때만) userData.href로 간다. mark(물체, 켜짐)으로 가리킨 표시를 한다
function clicks(el, renderer, camera, hits, mark) {
  const ray = new THREE.Raycaster(), ptr = new THREE.Vector2(), tip = el.querySelector('.s3-tip');
  let hover = null, down = null;
  const pick = e => {
    const r = renderer.domElement.getBoundingClientRect();
    ptr.set((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1);
    ray.setFromCamera(ptr, camera);
    return (ray.intersectObjects(hits, false)[0] || {}).object || null;
  };
  renderer.domElement.addEventListener('pointermove', e => {
    const o = pick(e);
    if (hover !== o) {
      if (hover) mark(hover, false);
      hover = o;
      if (o) mark(o, true);
    }
    renderer.domElement.style.cursor = o && o.userData.href ? 'pointer' : '';
    tip.hidden = !o;
    if (o) { const r = el.getBoundingClientRect(); tip.textContent = o.userData.title; tip.style.left = e.clientX - r.left + 12 + 'px'; tip.style.top = e.clientY - r.top + 12 + 'px'; }
  });
  renderer.domElement.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
  renderer.domElement.addEventListener('pointerup', e => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) return;
    const o = pick(e);
    if (o && o.userData.href) location.href = o.userData.href;
  });
}

export function mount(el) {
  const href = el.dataset.href, blocks = JSON.parse(el.dataset.blocks), cols = JSON.parse(el.dataset.cols);
  const { scene, renderer, camera, controls } = stage(el);
  const labels = new CSS2DRenderer();
  Object.assign(labels.domElement.style, { position: 'absolute', inset: '0', pointerEvents: 'none' });
  el.append(labels.domElement);

  //scene.add(hull());

  loadShip(scene, 0.8);

  // // 화물창(멤브레인, 팔각형 단면)과 트렁크 갑판 위 돔
  // const w = B / 2 - 3, oct = [[-(w - 4), DB], [w - 4, DB], [w, DB + 4], [w, D - 5], [w - 8, TD - 1], [-(w - 8), TD - 1], [-w, D - 5], [-w, DB + 4]];
  // const tankMat = new THREE.MeshStandardMaterial({ color: 0xBFCBE6, transparent: true, opacity: 0.28, depthWrite: false });
  // const solid = new THREE.MeshStandardMaterial({ color: 0xDFE3EA });
  // TANKS.forEach(([x0, x1, name]) => {
  //   const geo = new THREE.ExtrudeGeometry(new THREE.Shape(oct.map(([z, y]) => new THREE.Vector2(z, y))), { depth: x1 - x0, bevelEnabled: false });
  //   geo.rotateY(-Math.PI / 2); geo.translate(x1, 0, 0);
  //   const t = new THREE.Mesh(geo, tankMat);
  //   t.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: 0x5B6B8C, transparent: true, opacity: 0.6 })));
  //   scene.add(t);
  //   const c = (x0 + x1) / 2;
  //   for (const [dx, r] of [[-5, 1.8], [4, 1.3]]) {
  //     const dome = new THREE.Mesh(new THREE.CylinderGeometry(r, r, 3, 20), solid); dome.position.set(c + dx, TD + 1.5, 0); scene.add(dome);
  //   }
  //   scene.add(label(name + ' 화물창', '', c, TD + 8, 0));
  // });
  // scene.add(box(56, 273, D, TD, -(w - 6), w - 6, new THREE.MeshStandardMaterial({ color: 0xE9ECF2, transparent: true, opacity: 0.55 })));
  // // 선미 거주구·조타실·굴뚝, 압축기실
  // scene.add(box(12, 30, D, D + 18, -14, 14, solid), box(20, 31, D + 18, D + 22, -21, 21, solid), box(3, 11, D, D + 26, -4, 4, solid));
  // scene.add(box(62, 74, TD, TD + 5, -6, 6, solid));
  // 선저 블록(이중저): 블록 07~16 x 좌현/우현. 데이터가 있는 블록만 누를 수 있다
  // const hits = [];
  // cols.forEach((c, i) => {
  //   const x0 = BX0 + i * BL, x1 = x0 + BL, half = Math.min(B / 2 - 0.5, hb((x0 + x1) / 2) - 2);
  //   for (const side of ['P', 'S']) {
  //     const name = 'B' + c + side, on = name in blocks, s = side === 'P' ? -1 : 1;
  //     const m = box(x0 + 0.3, x1 - 0.3, kz(x0) + 0.2, kz(x0) + DB, side === 'P' ? -half : 0.3, side === 'P' ? -0.3 : half,
  //       new THREE.MeshStandardMaterial({ color: on ? BLUE : 0xC9CBD3, transparent: true, opacity: on ? 0.92 : 0.4, emissive: 0x000000 }));
  //     m.userData = on ? { name, title: `BLK ${name} · ${blocks[name]} 선저 · 평면도 열기`, href: href + name } : { name, title: `BLK ${name} · 예시 블록 (데이터 없음)` };
  //     scene.add(m); hits.push(m);
  //     if (on) scene.add(label(`${name} ${blocks[name]}`, 'on', (x0 + x1) / 2, -4, s * (B / 2 + 6)));
  //   }
  // });

  // ----------------------------------------------------
// 선저 블록 B07 ~ B16
// 실제 LNG GLB 위에 기존 클릭 영역을 그대로 유지
// ----------------------------------------------------
  const hits = [];

  // 블록이 실제 선박 안에서 잘 보이도록 높이 조정
  const BLOCK_Y0 = 2.2;
  const BLOCK_Y1 = 5.0;

  cols.forEach((c, i) => {

    const x0 = BX0 + i * BL;
    const x1 = x0 + BL;

    const half = floorHalf((x0 + x1) / 2);

    for (const side of ['P', 'S']) {

      const name = 'B' + c + side;
      const on = name in blocks;
      const s = side === 'P' ? -1 : 1;

      const material = new THREE.MeshStandardMaterial({
        color: on ? BLUE : 0xC9CBD3,
        transparent: true,

        // 실제 데이터 있는 블록만 눈에 띄게
        opacity: on ? 0.92 : 0.10,

        emissive: 0x000000,

        // 실제 GLB보다 블록이 잘 보이게
        depthWrite: false
      });

      const m = box(
        x0 + 0.3,
        x1 - 0.3,

        // Y 위치
        BLOCK_Y0,
        BLOCK_Y1,

        // 좌현 / 우현
        side === 'P'
          ? -half
          : 0.3,

        side === 'P'
          ? -0.3
          : half,

        material
      );

      // 실제 선박보다 위에 그리기
      m.renderOrder = 3;

      // 기존 클릭 / hover 정보 그대로 유지
      m.userData = on
        ? {
            name,
            title: `BLK ${name} · ${blocks[name]} 선저 · 평면도 열기`,
            href: href + name
          }
        : {
            name,
            title: `BLK ${name} · 예시 블록 (데이터 없음)`
          };

      scene.add(m);
      hits.push(m);

      // 실제 데이터 있는 B12P / B12S 등만 라벨 표시
      if (on) {
        scene.add(
          label(
            `${name} ${blocks[name]}`,
            'on',
            (x0 + x1) / 2,
            BLOCK_Y1 + 3,
            s * (B / 2 + 6)
          )
        );
      }
    }
  });

  scene.add(label('선수 FWD →', '', L + 14, D, 0), label('선미', '', -12, D, 0));
  const grid = new THREE.GridHelper(700, 35, 0xD5DAE5, 0xE6E9F0); grid.position.set(150, -12, 0); scene.add(grid);

  // 시점: 2D 도면처럼 우현에서 본다(선수가 오른쪽). 기본(위에서 비스듬히) / 측면 / 아래에서(선저 블록이 잘 보임)
  const views = { iso: [[110, 112, 235], [152, 14, 0]], side: [[152, 24, 380], [152, 12, 0]], below: [[120, -150, 190], [160, 2, 0]] };
  // 화면이 좁으면(폰) 배 전체가 들어오도록 카메라를 뒤로 물린다
  const setView = v => {
    const [p, t] = views[v], k = Math.max(1, 2.1 / camera.aspect);
    camera.position.set(...p.map((c, i) => t[i] + (c - t[i]) * k)); controls.target.set(...t); controls.update();
  };
  el.querySelectorAll('[data-view]').forEach(b => b.onclick = () => setView(b.dataset.view));

  const resize = () => {
    const { width, height } = el.getBoundingClientRect();
    renderer.setSize(width, height); labels.setSize(width, height);
    camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(el); resize(); setView('iso');

  clicks(el, renderer, camera, hits, (o, on) => o.material.emissive.setHex(on ? (o.userData.href ? 0x1030AA : 0x222222) : 0x000000));

  renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); labels.render(scene, camera); });
  el.dataset.ready = '1';
  el.querySelector('.s3-msg').remove();
}

// 데이터 선순환 화면: LNG선 도안에 기록 칸(cols × rows)만 그린다. 밸러스트 탱크가 화물창을 둘러싸는 모양대로 좌현 선측 2줄 · 이중저 4줄 · 우현 선측 2줄.
// 칸 색과 빨간 점은 화면 쪽에서 바꾼다. 본문이 CSS zoom 안에 있어서 캔버스 크기는 CSS(100%)에 맡기고 드로잉 버퍼만 맞춘다
export function mountLoop(el, cols, rows) {
  const { scene, renderer, camera, controls } = stage(el);
  loadShip(scene);
  const cw = 10 * BL / cols, side = 2, H = 8.6, tiles = [], unit = new THREE.BoxGeometry(cw - 0.9, 1, 1);
  for (let i = 0; i < cols * rows; i++) {
    const r = i % rows, x = BX0 + (Math.floor(i / rows) + 0.5) * cw;
    const m = new THREE.Mesh(unit, new THREE.MeshBasicMaterial({ color: 0xD3D8E0, transparent: true }));  // 선체에 가리지 않게 맨 나중에 그린다
    if (r < side || r >= rows - side) {
      const k = r < side ? r : rows - 1 - r;
      m.position.set(x, 24.2 - (k + 0.5) * H, (r < side ? -1 : 1) * sideHalf(x)); m.scale.set(1, H - 0.7, 0.4);
    } else {
      const half = floorHalf(x), ch = 2 * half / (rows - 2 * side);
      m.position.set(x, 3.4, -half + (r - side + 0.5) * ch); m.scale.set(1, 1.6, ch - 0.7);
    }
    m.renderOrder = 3; scene.add(m); tiles.push(m);
  }
  const marks = new THREE.Group(), dot = new THREE.SphereGeometry(2.4, 12, 10), red = new THREE.MeshBasicMaterial({ color: 0xDC2626, transparent: true });
  scene.add(marks);
  const resize = () => {
    const { width, height } = el.getBoundingClientRect();
    renderer.setSize(width, height, false);
    camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix();
    const k = Math.max(1, 1.9 / camera.aspect), t = [150, 10, 0];
    camera.position.set(...[84, 170, 215].map((c, i) => t[i] + (c - t[i]) * k)); controls.target.set(...t); controls.update();
  };
  new ResizeObserver(resize).observe(el); resize();
  renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
  el.dataset.ready = '1';
  return {
    paint: (i, color) => tiles[i] && tiles[i].material.color.set(color),
    mark: i => {
      const t = tiles[i], m = new THREE.Mesh(dot, red), j = () => Math.random() - 0.5;
      if (!t) return;
      m.position.copy(t.position).add(new THREE.Vector3(j() * cw * 0.6, j() * t.scale.y * 0.6 + (t.scale.z > 1 ? 3 : 0), j() * t.scale.z * 0.6));
      m.renderOrder = 4; marks.add(m);
    },
    clear: () => marks.clear(),
  };
}

// 품질 분석: 블록 평면도(SVG)의 칸을 그대로 읽어 철판을 3D로 세운다. 칸 위치·색·측정점이 평면도와 항상 같다.
// 칸 하나 = 1.7 m × 1.7 m(2프레임 × 2론지). 탱크 안쪽(Z)과 호퍼(H)는 론지·늑판·거더가 보이고, 내저판 윗면(D)·선저 외판(O)은 매끈한 면이다
export function mountPlate(el, svg) {
  const { scene, renderer, camera, controls } = stage(el);
  const labels = new CSS2DRenderer();
  Object.assign(labels.domElement.style, { position: 'absolute', inset: '0', pointerEvents: 'none' });
  el.append(labels.domElement);
  const gut = svg.querySelector('.gut'), rects = [...svg.querySelectorAll('a > rect.z')], names = svg.querySelectorAll('.zl > g'), M = 1.7;
  const X0 = +gut.getAttribute('x'), Y0 = +gut.getAttribute('y'), CW = +rects[0].getAttribute('width') + 7, CH = +rects[0].getAttribute('height') + 7;
  const W = +gut.getAttribute('width') / CW * M, H = +gut.getAttribute('height') / CH * M, hits = [];
  scene.add(box(-0.2, W + 0.2, -0.08, 0, -0.2, H + 0.2, new THREE.MeshStandardMaterial({ color: 0xDDE1E8 })));

  const unit = new THREE.BoxGeometry(M - 0.1, 0.03, M - 0.1), dot = new THREE.SphereGeometry(0.1, 12, 10);
  rects.forEach((r, i) => {
    const a = r.parentNode, css = getComputedStyle(r), x = (+r.getAttribute('x') - 3.5 - X0) / CW * M + M / 2, z = (+r.getAttribute('y') - 3.5 - Y0) / CH * M + M / 2;
    const m = new THREE.Mesh(unit, new THREE.MeshBasicMaterial({ color: a.classList.contains('qbad') ? css.stroke : css.fill }));
    m.position.set(x, 0.02, z);
    m.userData = { title: a.querySelector('title').textContent, href: a.getAttribute('href') };
    scene.add(m); hits.push(m);
    scene.add(label([...names[i].querySelectorAll('.id, .st')].map(t => t.textContent).join(' '), '', x, 0.5, z));
  });
  svg.querySelectorAll('.dots circle').forEach(c => {
    const m = new THREE.Mesh(dot, new THREE.MeshBasicMaterial({ color: getComputedStyle(c).fill }));
    m.position.set((+c.getAttribute('cx') - X0) / CW * M, 0.45, (+c.getAttribute('cy') - Y0) / CH * M);
    m.userData = { title: c.querySelector('title').textContent };
    scene.add(m); hits.push(m);
  });
  if ('ZH'.includes(el.dataset.s)) {
    const steel = new THREE.MeshStandardMaterial({ color: 0xC2C9D6 });
    const ghost = new THREE.MeshStandardMaterial({ color: 0xC4CDDC, transparent: true, opacity: 0.18, depthWrite: false, side: THREE.DoubleSide });
    for (let z = 0; z <= H + 0.01; z += M / 2) scene.add(box(0, W, 0.03, 0.24, z - 0.012, z + 0.012, steel), box(0, W, 0.24, 0.26, z - 0.05, z + 0.05, steel));  // 론지(T형)
    for (let x = 0; x <= W + 0.01; x += M * 2) scene.add(box(x - 0.02, x + 0.02, 0, 1.5, 0, H, ghost));  // 늑판(4프레임마다)
    scene.add(box(0, W, 0, 1.5, H / 2 - 0.02, H / 2 + 0.02, ghost));  // 거더(L4)
  }
  scene.add(label('FWD →', '', W + 0.9, 0.2, H / 2));

  camera.near = 0.1; controls.minDistance = 3; controls.maxDistance = 40; controls.maxPolarAngle = Math.PI / 2 - 0.05;
  const resize = () => {
    const { width, height } = el.getBoundingClientRect();
    renderer.setSize(width, height); labels.setSize(width, height);
    camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix();
    const k = Math.max(0.82, 1.6 / camera.aspect);
    camera.position.set(W / 2 - 3.5 * k, 9.5 * k, H / 2 + 12.5 * k); controls.target.set(W / 2, 0, H / 2); controls.update();
  };
  new ResizeObserver(resize).observe(el); resize();
  clicks(el, renderer, camera, hits, (o, on) => o.scale.set(on ? 1.05 : 1, on ? 5 : 1, on ? 1.05 : 1));
  renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); labels.render(scene, camera); });
  el.dataset.ready = '1';
}
