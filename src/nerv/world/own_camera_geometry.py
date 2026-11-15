"""Capture-time robot shape in its own camera frame, without scene semantics.

Ground envelopes project conservative collision bounds onto the plane through
the lowest declared support collision bound, normal to gravity. The plane is not
a measured sole or contact surface. This is not floor detection, target membership,
obstacle clearance, or a claim that projected points are visible.
"""
import math

import mujoco
import numpy as np


def _corners(model, data, geoms):
    signs = np.array([[x,y,z] for x in (-1,1) for y in (-1,1) for z in (-1,1)])
    boxes = model.geom_aabb[geoms]
    local = boxes[:,None,:3] + signs[None,:,:] * boxes[:,None,3:]
    rotation = data.geom_xmat[geoms].reshape(-1,3,3)
    return (np.einsum('nij,nkj->nki',rotation,local) + data.geom_xpos[geoms,None,:]).reshape(-1,3)


def _hull(points):
    values = sorted(set(map(tuple, points)))
    if len(values) < 3:
        raise ValueError('Degenerate physical ground envelope')

    def cross(a,b,c):
        return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])

    lower,upper = [],[]
    for collection,ordered in ((lower,values),(upper,reversed(values))):
        for point in ordered:
            while len(collection)>=2 and cross(collection[-2],collection[-1],point)<=0:
                collection.pop()
            collection.append(point)
    return np.asarray(lower[:-1]+upper[:-1])


def _projection(model, camera, points, width, height):
    """Pixel locations only: frustum projection does not establish visibility."""
    if model.cam_sensorsize[camera,0] > 0:
        return {'valid':False,'reason':'sensor_size_intrinsics_not_supported'}
    span = float(model.cam_fovy[camera])
    ortho = model.cam_projection[camera] == mujoco.mjtProjection.mjPROJ_ORTHOGRAPHIC
    if not math.isfinite(span) or span<=0 or (not ortho and span>=180) or width<=0 or height<=0:
        return {'valid':False,'reason':'invalid_camera_optics'}
    scale = height/span if ortho else height/(2*math.tan(math.radians(span)/2))
    pixels,in_front,in_frame = [],[],[]
    for x,y,z in points:
        front = bool(-z>0)
        uv = [width/2+scale*x/(1 if ortho else -z),height/2-scale*y/(1 if ortho else -z)] if front else None
        if uv is not None and not all(math.isfinite(v) for v in uv):
            uv = None
        pixels.append(uv)
        in_front.append(front)
        in_frame.append(bool(uv is not None and 0<=uv[0]<width and 0<=uv[1]<height))
    return {'valid':True,'points_px':pixels,'in_front':in_front,'in_frame_projection':in_frame,
            'pixel_axes':{'right':'+X','down':'+Y'},'occlusion_evaluated':False,
            'meaning':'projection only; a point may be hidden by self or scenery or clipped'}


def capture_geometry(model, data, root, camera, width, height, support_body_names):
    """Caller holds the same physics lock used to update the raw camera scene."""
    stamp = float(data.time)
    unavailable = {'schema_revision':1,'valid':False,'sample_sim_time':stamp if math.isfinite(stamp) else None}
    try:
        if not math.isfinite(stamp):
            raise ValueError('Invalid image sample time')
        if not 1<=root<model.nbody or not 0<=camera<model.ncam:
            raise ValueError('Invalid controlled camera or robot')
        if int(model.body_rootid[model.cam_bodyid[camera]])!=root:
            raise ValueError('Camera does not belong to controlled robot')
        if model.cam_mode[camera]!=mujoco.mjtCamLight.mjCAMLIGHT_FIXED:
            raise ValueError('Camera is not robot fixed')
        gravity = np.asarray(model.opt.gravity,dtype=float)
        norm = float(np.linalg.norm(gravity))
        if not math.isfinite(norm) or norm<=0:
            raise ValueError('Gravity reference unavailable')
        up = -gravity/norm
        physical = (model.geom_contype!=0)|(model.geom_conaffinity!=0)
        geoms = np.flatnonzero(physical & (model.body_rootid[model.geom_bodyid]==root))
        if not len(geoms) or not support_body_names:
            raise ValueError('Own physical body and declared support geometry required')
        feet = {}
        for name in support_body_names:
            bid = mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_BODY,name)
            if bid<=0 or int(model.body_rootid[bid])!=root:
                raise ValueError('Support geometry does not belong to controlled robot')
            selected = []
            for geom in geoms:
                parent = int(model.geom_bodyid[geom])
                while parent and parent!=bid:
                    parent = int(model.body_parentid[parent])
                if parent==bid:
                    selected.append(int(geom))
            if not selected:
                raise ValueError('Declared support body has no physical geometry')
            label = name.rsplit('/',1)[-1]
            if label in feet:
                raise ValueError('Support body labels must be distinct')
            feet[label] = _corners(model,data,np.asarray(selected))
        corners = _corners(model,data,geoms)
        if not np.isfinite(corners).all() or not all(np.isfinite(p).all() for p in feet.values()):
            raise ValueError('Nonfinite own geometry')
        forward = data.xmat[root].reshape(3,3)[:,0].copy()
        forward -= up*np.dot(up,forward)
        norm = float(np.linalg.norm(forward))
        if not math.isfinite(norm) or norm<=np.finfo(float).eps:
            raise ValueError('Gravity aligned body frame unavailable')
        forward /= norm
        left = np.cross(up,forward)
        basis = np.array([forward,left,up])
        base = data.xpos[root]
        support_bound = float(min((p@up).min() for p in feet.values()))
        origin = base+up*(support_bound-float(np.dot(base,up)))
        camera_rotation = data.cam_xmat[camera].reshape(3,3)
        camera_position = data.cam_xpos[camera]

        def camera_frame(points):
            result = (points-camera_position)@camera_rotation
            if not np.isfinite(result).all():
                raise ValueError('Nonfinite camera transform')
            return result

        def envelope(points):
            xy = _hull(((points-base)@basis.T)[:,:2])
            projected = camera_frame(origin+xy[:,0,None]*forward+xy[:,1,None]*left)
            return {'points_camera_m':projected.tolist(),
                    'pixel_projection':_projection(model,camera,projected,width,height)}

        body_camera = camera_frame(corners)
        return {'schema_revision':1,'valid':True,'sample_sim_time':float(data.time),
                'source':'own_collision_geometry_and_measured_articulation','frame':'camera',
                'geometry_representation':'conservative_collision_bounds',
                'contact_evaluated':False,'floor_detection_evaluated':False,'occlusion_evaluated':False,
                'camera_axes':{'right':'+X','up':'+Y','view':'-Z'},
                'body_bounds_camera_m':[body_camera.min(axis=0).tolist(),body_camera.max(axis=0).tolist()],
                'projection_plane':{'source':'lowest_declared_support_collision_bound_and_gravity; not detected floor',
                    'origin_camera_m':camera_frame(origin[None,:])[0].tolist(),
                    'up_camera':(up@camera_rotation).tolist()},
                'body_ground_envelope':envelope(corners),
                'foot_ground_envelopes':{name:envelope(points) for name,points in feet.items()},
                'meaning':'own conservative shape bounds only; support bounds are not measured soles or contacts; pixels and a projected envelope do not certify visible floor or arrival'}
    except (ValueError,TypeError,IndexError,OverflowError) as error:
        return {**unavailable,'reason':'own_geometry_unavailable','message':str(error)}
