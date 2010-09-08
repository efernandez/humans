import roslib; roslib.load_manifest('hai_sandbox')
import rospy

import hrl_lib.prob as pb
import hrl_lib.util as ut
import hai_sandbox.pr2 as pr2
import hai_sandbox.msg as hm
import pr2_msgs.msg as pm
import sensor_msgs.msg as sm
import scipy.spatial as sp
import actionlib

import tf.transformations as tr
import hrl_lib.transforms as htf
import hrl_lib.rutils as ru
import numpy as np
import time
import pdb
import sys
import visualization_msgs.msg as vm
import geometry_msgs.msg as gm
import std_msgs.msg as sdm
import time
import hrl_lib.tf_utils as tfu
import tf
import cv
from cv_bridge.cv_bridge import CvBridge, CvBridgeError
import hai_sandbox.features as fea
import hai_sandbox.bag_processor as bp
import hrl_camera.ros_camera as rc
import math
import hrl_pr2_lib.devices as hpr2


def dict_to_arm_arg(d):
    trans = [d['pose']['position']['x'], 
             d['pose']['position']['y'], 
             d['pose']['position']['z']]
    rot = [d['pose']['orientation']['x'],
           d['pose']['orientation']['y'],
           d['pose']['orientation']['z'],
           d['pose']['orientation']['w']]
    return [trans, rot, d['header']['frame_id'], d['header']['stamp']]


def create_mdict():
    mdict = {}
    mdict['arrow']            = vm.Marker.ARROW
    mdict['cube']             = vm.Marker.CUBE
    mdict['sphere']           = vm.Marker.SPHERE
    mdict['cylinder']         = vm.Marker.CYLINDER
    mdict['line_strip']       = vm.Marker.LINE_STRIP
    mdict['line_list']        = vm.Marker.LINE_LIST
    mdict['cube_list']        = vm.Marker.CUBE_LIST
    mdict['sphere_list']      = vm.Marker.SPHERE_LIST
    mdict['points']           = vm.Marker.POINTS
    mdict['text_view_facing'] = vm.Marker.TEXT_VIEW_FACING
    mdict['mesh_resource']    = vm.Marker.MESH_RESOURCE
    mdict['triangle_list']    = vm.Marker.TRIANGLE_LIST
    return mdict


def list_marker(points, colors, scale, mtype, mframe, duration=10.0, m_id=0):
    m = vm.Marker()
    m.header.frame_id = mframe
    m.id = m_id
    m.type = create_mdict()[mtype]
    m.action = vm.Marker.ADD
    m.points = [gm.Point(points[0,i], points[1,i], points[2,i]) for i in range(points.shape[1])]
    #pdb.set_trace()
    m.colors = [sdm.ColorRGBA(colors[0,i], colors[1,i], colors[2,i], colors[3,i]) for i in range(colors.shape[1])]

    m.color.r = 1.
    m.color.g = 0.
    m.color.b = 0.
    m.color.a = 1.
    m.scale.x = scale[0]
    m.scale.y = scale[1]
    m.scale.z = scale[2]
    #m.pose.orientation.x = 0
    #m.pose.orientation.y = 0
    #m.pose.orientation.z = 0
    #m.pose.orientation.w = 1.
    

    m.lifetime = rospy.Duration(duration)

    return m


def single_marker(point, orientation, mtype, mframe, scale=[.2,.2,.2], color=[1.0, 0, 0,.5], duration=10.0, m_id=0):
    m = vm.Marker()
    m.header.frame_id = mframe
    m.id = m_id
    m.type = create_mdict()[mtype]
    m.action = vm.Marker.ADD

    m.pose.position.x = point[0,0]
    m.pose.position.y = point[1,0]              
    m.pose.position.z = point[2,0]              
    m.pose.orientation.x = orientation[0,0]
    m.pose.orientation.y = orientation[1,0]
    m.pose.orientation.z = orientation[2,0]
    m.pose.orientation.w = orientation[3,0]

    m.scale.x = scale[0]
    m.scale.y = scale[1]
    m.scale.z = scale[2]
    m.color.r = color[0]
    m.color.g = color[1]
    m.color.b = color[2]
    m.color.a = color[3]
    m.lifetime = rospy.Duration(duration)
    return m


def match_image(descriptors, image, threshold=.6):
    cgray = fea.grayscale(image)
    c_loc, c_desc = fea.surf(cgray)
    features_db = sp.KDTree(np.array(descriptors))
    matched = []
    for i, desc in enumerate(c_desc):
        dists, idxs = features_db.query(np.array(desc), 2)
        ratio = dists[0] / dists[1]
        if ratio < threshold:
            matched.append({'model': descriptors[idxs[0]],
                            'model_idx': idxs[0],
                            'candidate': desc,
                            'candidate_loc': c_loc[i]})
    return matched


def normalize_ang(a):
    while a < 0:
        a = a + (2*np.pi)
    while a > (2*np.pi):
        a = a - (2*np.pi)
    return a
    

def find_object_pose(image, point_cloud_msg, data_dict, pro_T_bf, cam_info, RADIUS=.1):
    ## detect surf features, then match with features in model
    desc = data_dict['start_conditions']['pose_parameters']['descriptors']
    match_info = match_image(desc, image, .6)
    match_idxs = [r['model_idx'] for r in match_info]
    match_locs = [r['candidate_loc'] for r in match_info]
    rospy.loginfo('Matched %d out of %d descriptors' % (len(match_info), len(desc)))
    if len(match_info) < 2:
        raise RuntimeError('Insufficient number of matches')

    ## convert surf features into 3d in baseframe
    # project into camera frame
    point_cloud_bf = ru.pointcloud_to_np(point_cloud_msg)
    point_cloud_pro = tfu.transform_points(pro_T_bf, point_cloud_bf)
    point_cloud_2d_pro, point_cloud_reduced_pro = bp.project_2d_bounded(cam_info, point_cloud_pro)

    # find the center point implied by surf points 
    model_directions = np.matrix(data_dict['start_conditions']['pose_parameters']['surf_pose_dir2d'][:, match_idxs])
    match_locs_mat = np.column_stack([np.matrix(l[0]).T for l in match_locs])

    expected_position_set2d = match_locs_mat + model_directions
    #expected_position2d = np.mean(expected_position_set2d, 1)
    expected_position2d = np.median(expected_position_set2d, 1)
    expected_position2d = np.column_stack([expected_position2d, expected_position_set2d])
    expected_position_locs = [[[expected_position2d[0,i], expected_position2d[1,i]]] for i in range(expected_position2d.shape[1])]
    #expected_position_loc = [expected_positions2d[0,0], expected_positions2d[1,0]]
    expected_position3d_pro = np.matrix(bp.assign_3d_to_surf(expected_position_locs,
        point_cloud_reduced_pro, point_cloud_2d_pro))
    expected_positions3d_bf = tfu.transform_points(np.linalg.inv(pro_T_bf), expected_position3d_pro)

    # assign 3d location to each surf feature
    matched_surf_loc3d_pro = np.matrix(bp.assign_3d_to_surf(match_locs, point_cloud_reduced_pro, point_cloud_2d_pro))
    matched_surf_loc3d_bf  = tfu.transform_points(np.linalg.inv(pro_T_bf), matched_surf_loc3d_pro)
  
    ## find the normal component
    #center_bf = np.mean(matched_surf_loc3d_bf, 1) #find center by voting.
    center_bf = expected_positions3d_bf[:,0]
    point_cloud_kd_bf = sp.KDTree(point_cloud_bf.T)
    neighbor_idxs = point_cloud_kd_bf.query_ball_point(np.array(center_bf.T), RADIUS)
    points_nearby_bf = point_cloud_bf[:, neighbor_idxs[0]]

    #Instead of using the 3D location of matched SURF features, use the local neighborhood of points
    #a_frame_bf = bp.create_frame(matched_surf_loc3d_bf, p= -center_bf)
    a_frame_bf = bp.create_frame(points_nearby_bf, p= -center_bf)
    normal_bf = a_frame_bf[:,2]
    null_bf = a_frame_bf[:,0:2]


    ####################################################################################################
    ####################################################################################################
    #drad = cand_rad_angs[0]

    #surf_dir_obj = frame_bf.T * bf_R_pro * np.matrix([np.cos(drad), np.sin(drad), 0.]).T 
    #delta_theta = math.atan2(surf_dir_obj[1,0], surf_dir_obj[0,0])

    # bf_R_pro = (data_dict['start_conditions']['pro_T_bf'][0:3,0:3]).T
    # frame_bf = data_dict['start_conditions']['pose_parameters']['frame_bf']
    # x_bf = frame_bf[:,0]
    # x_pro = bf_R_pro.T * x_bf
    # x_ang_pro = math.atan2(x_pro[1,0], x_pro[0,0])
    #rospy.loginfo('original x angle in prosilica frame is %.3f' % np.degrees(x_ang_pro))

    #model_delta_thetas = []
    #for loc, lap, size, direction, hess in data_dict['start_conditions']['pose_parameters']['surf_loc2d_pro']:
    #    delta_theta = ut.standard_rad(np.radians(direction) - x_ang_pro)
    #    model_delta_thetas.append(delta_theta)
    #    #x_ang_pro_recovered = ut.standard_rad(np.radians(direction) - delta_theta)

    #print 'object x axis is at angle %f in prosilica frame'% (x_ang_pro)
    #print 'feature is at %f in prosilica frame' % np.degrees(drad)
    #print 'the difference is %f' % np.degrees(delta_theta)

    #print delta_theta
    #print drad

    #pdb.set_trace()
    ####################################################################################################
    ####################################################################################################
    #for each surf direction in prosilica frame, give a direction of the x axis in prosilica frame
    model_delta_thetas = np.array(data_dict['start_conditions']['pose_parameters']['surf_directions'])[match_idxs]
    cand_rad_angs = np.array([np.radians(d) for loc, lap, size, d, hess in match_locs])
    hypothesized_angles = np.array([ut.standard_rad(cand_rad_angs[i] - model_delta_thetas[i]) for i in range(len(model_delta_thetas))])

    #average these directions
    x_vec_hat_pro = np.matrix([np.mean(np.cos(hypothesized_angles)), np.mean(np.sin(hypothesized_angles)), 0.]).T
        
    #convert to base frame, call this x_hat
    x_hat_bf = np.linalg.inv(pro_T_bf[0:3,0:3]) * x_vec_hat_pro

    #project x into the null space of our surf point cloud
    x_hat_null_bf = null_bf * np.linalg.inv(null_bf.T * null_bf) * null_bf.T * x_hat_bf

    #cross x_hat with normal
    y_bf = np.cross(normal_bf.T, x_hat_null_bf.T).T
    surf_vecs_bf = np.linalg.inv(pro_T_bf[0:3,0:3]) * np.row_stack([np.cos(hypothesized_angles),
                                                                    np.sin(hypothesized_angles),
                                                                    np.zeros((1,len(hypothesized_angles)))])
    display_dict = {'surf_vecs_bf': surf_vecs_bf,
                    'surf_loc3d_pro': matched_surf_loc3d_pro,
                    'expected_positions3d_bf': expected_positions3d_bf}

    rospy.loginfo('Observed normal: %s' % str(normal_bf.T))
    rospy.loginfo('Inferred x direction: %s' % str(x_hat_bf.T))

    #Normalize
    x_hat_null_bf = x_hat_null_bf / np.linalg.norm(x_hat_null_bf)
    y_bf = y_bf / np.linalg.norm(y_bf)
    return np.column_stack([x_hat_null_bf, y_bf, normal_bf]), center_bf, display_dict

def create_frame_marker(center, frame, line_len, frame_id):
    clist = []
    plist = []
    alpha = line_len
    for i in range(3):
        colors = np.matrix(np.zeros((4,2)))
        colors[i,:] = 1.0
        colors[3,:] = 1.0
        clist.append(colors)
        plist.append(np.column_stack([center, center+ alpha * frame[:,i]]))
    return list_marker(np.column_stack(plist), np.column_stack(clist), [.01, 0, 0], 'line_list', frame_id)

def cv_to_ros(cvimg, frame_id):
    bridge = CvBridge()
    rosimg = bridge.cv_to_imgmsg(cvimg)
    rosimg.header.frame_id = frame_id
    return rosimg

class DisplayRecordedPoseReduced: 
    def __init__(self, online, seconds_to_run):
        self.online = online
        self.seconds_to_run = seconds_to_run
        self.marker_pub = rospy.Publisher('object_frame', vm.Marker)
        self.point_cloud_pub = rospy.Publisher('original_point_cloud', sm.PointCloud)
        self.surf_pub = rospy.Publisher('surf_features', sm.PointCloud)
        self.expected_positions3d_pub = rospy.Publisher('expected_position3d_bf', sm.PointCloud)
        self.left_arm_trajectory_pub = rospy.Publisher('left_arm_trajectory', sm.PointCloud)
        self.right_arm_trajectory_pub = rospy.Publisher('right_arm_trajectory', sm.PointCloud)
        self.contact_pub = rospy.Publisher('contact_bf', vm.Marker)
        self.fine_pose = rospy.Publisher('base_pose_obj', gm.PoseStamped)
        
        if not self.online:
            self.proc_pub = rospy.Publisher('/prosilica/image_rect_color', sm.Image)
            self.caminfo = rospy.Publisher('/prosilica/camera_info', sm.CameraInfo)
            self.tfbroadcast = tf.TransformBroadcaster()
    
    def display(self, frame_bf, center_bf,
        point_cloud_bf, surf_loc3d_pro, proc_img, expected_position3d_bf, pro_T_bf,
        left_arm_trajectory, right_arm_trajectory, fine_position_bf, contact_bf=None):

        frame_marker      = create_frame_marker(center_bf, frame_bf, .4, 'base_footprint')
        proc_image_msg    = cv_to_ros(proc_img, 'high_def_optical_frame')
        proc_cam_info_msg = ut.load_pickle('prosilica_caminfo.pkl')
        surf_pc           = ru.np_to_pointcloud(surf_loc3d_pro, 'high_def_optical_frame')
        expected_position3d_bf = ru.np_to_pointcloud(expected_position3d_bf, 'base_footprint')
        left_arm_traj_bf  = ru.np_to_pointcloud(left_arm_trajectory, 'base_footprint')
        right_arm_traj_bf = ru.np_to_pointcloud(right_arm_trajectory, 'base_footprint')

        ps_fine_position_bf = gm.PoseStamped()
        ps_fine_position_bf.header.frame_id = '/base_footprint'
        ps_fine_position_bf.pose.position.x = fine_position_bf[0][0]
        ps_fine_position_bf.pose.position.y = fine_position_bf[0][1]
        ps_fine_position_bf.pose.position.z = fine_position_bf[0][2]
        ps_fine_position_bf.pose.orientation.x = fine_position_bf[1][0] 
        ps_fine_position_bf.pose.orientation.y = fine_position_bf[1][1] 
        ps_fine_position_bf.pose.orientation.z = fine_position_bf[1][2] 
        ps_fine_position_bf.pose.orientation.w = fine_position_bf[1][3] 

        if contact_bf != None:
            contact_marker = single_marker(contact_bf, np.matrix([0,0,0,1.]).T, 'sphere', 'base_footprint', scale=[.02, .02, .02])
        else:
            contact_marker = None

        self.publish_messages(frame_marker, proc_image_msg, proc_cam_info_msg,
                surf_pc, point_cloud_bf, expected_position3d_bf, pro_T_bf,
                left_arm_traj_bf, right_arm_traj_bf, ps_fine_position_bf, contact_marker)

    def publish_messages(self, frame_marker, proc_image_msg, proc_cam_info_msg,
            surf_pc, point_cloud_bf, expected_position3d_bf, pro_T_bf,
            left_arm_traj_bf, right_arm_traj_bf, fine_position_bf, contact_marker = None):
        start_time = time.time()

        r = rospy.Rate(10.)
        while not rospy.is_shutdown():
            frame_marker.header.stamp = rospy.get_rostime()
            point_cloud_bf.header.stamp = rospy.get_rostime()
            proc_image_msg.header.stamp = rospy.get_rostime()
            proc_cam_info_msg.header.stamp = rospy.get_rostime()
            surf_pc.header.stamp = rospy.get_rostime()
            expected_position3d_bf.header.stamp = rospy.get_rostime()
            left_arm_traj_bf.header.stamp = rospy.get_rostime()
            right_arm_traj_bf.header.stamp = rospy.get_rostime()
            fine_position_bf.header.stamp = rospy.get_rostime()
            if contact_marker != None:
                contact_marker.header.stamp = rospy.get_rostime()

            #print 'publishing.'
            self.marker_pub.publish(frame_marker)
            self.point_cloud_pub.publish(point_cloud_bf)
            self.surf_pub.publish(surf_pc)
            self.expected_positions3d_pub.publish(expected_position3d_bf)
            self.left_arm_trajectory_pub.publish(left_arm_traj_bf)
            self.right_arm_trajectory_pub.publish(right_arm_traj_bf)
            self.fine_pose.publish(fine_position_bf)
            if contact_marker != None:
                self.contact_pub.publish(contact_marker)

            if not self.online:
                self.proc_pub.publish(proc_image_msg)
                self.caminfo.publish(proc_cam_info_msg)
                # Publish tf between point cloud and pro
                t, r = tfu.matrix_as_tf(np.linalg.inv(pro_T_bf))
                self.tfbroadcast.sendTransform(t, r, rospy.Time.now(), '/high_def_optical_frame', "/base_footprint")

            time.sleep(.1)
            if (time.time() - start_time) > self.seconds_to_run:
                break


class DisplayRecordedPose: 
    def __init__(self):
        rospy.init_node('display_pose')
        self.marker_pub = rospy.Publisher('object_frame', vm.Marker)
        self.point_cloud_pub = rospy.Publisher('original_point_cloud', sm.PointCloud)
        self.surf_pub = rospy.Publisher('surf_features', sm.PointCloud)
        self.proc_pub = rospy.Publisher('/prosilica/image_rect_color', sm.Image)
        self.caminfo = rospy.Publisher('/prosilica/camera_info', sm.CameraInfo)
        self.contact_pub = rospy.Publisher('contact_bf', vm.Marker)
        self.tfbroadcast = tf.TransformBroadcaster()

    def display_original_object_pose(self, data_fname):
        print 'loading pickle'
        data_dict = ut.load_pickle(data_fname)
        surf_loc3d_pro     = data_dict['start_conditions']['pose_parameters']['surf_loc3d_pro']
        surf_loc2d_pro     = data_dict['start_conditions']['pose_parameters']['surf_loc2d_pro']
        contact_bf         = data_dict['start_conditions']['pose_parameters']['contact_bf']
        point_cloud_2d_pro = data_dict['start_conditions']['pose_parameters']['point_cloud_2d_pro']
        frame_bf           = data_dict['start_conditions']['pose_parameters']['frame_bf']
        center_bf          = data_dict['start_conditions']['pose_parameters']['center_bf']

        model_file_name    = data_dict['start_conditions']['model_image']
        pc                 = data_dict['start_conditions']['points']
        surf_cloud_2d_pro  = data_dict['start_conditions']['camera_info'].project(surf_loc3d_pro[0:3,:])

        rospy.loginfo('Original normal (base_footprint): %s' % str(frame_bf[:,2].T))
        rospy.loginfo('Original x direction (base_footprint): %s' % str(frame_bf[:,0].T))

        rospy.loginfo('SURF directions')
        for e in data_dict['start_conditions']['pose_parameters']['surf_directions']:
            print np.degrees(e)

        ## Draw image with SURF and 3D points
        discrete_loc = np.array(np.round(point_cloud_2d_pro), dtype='int')
        proc_np = np.asarray(cv.LoadImageM(model_file_name))
        proc_np[discrete_loc[1,:], discrete_loc[0,:]] = 0
        proc_cv = cv.fromarray(proc_np)
        cv.SaveImage('proc_3d_orig_surf.png', fea.draw_surf(proc_cv, surf_loc2d_pro, (200, 0, 0)))

        ## Draw image with 3D matched surf features
        nslocs = []
        for idx, sloc in enumerate(surf_loc2d_pro):
            loc, lap, size, d, hess = sloc
            nslocs.append(((surf_cloud_2d_pro[0,idx], surf_cloud_2d_pro[1,idx]), lap, size, d, hess))
        cv.SaveImage('proc_3d_proj_surf.png', fea.draw_surf(proc_cv, nslocs, (0, 200, 0)))

        frame_marker      = create_frame_marker(center_bf, frame_bf, .4, 'base_footprint')
        proc_image_msg    = cv_to_ros(cv.LoadImage(model_file_name), 'high_def_optical_frame')
        proc_cam_info_msg = ut.load_pickle('prosilica_caminfo.pkl')
        contact_marker    = single_marker(contact_bf, np.matrix([0,0,0,1.]).T, 'sphere', 'base_footprint', scale=[.02, .02, .02])
        surf_pc           = ru.np_to_pointcloud(surf_loc3d_pro, 'high_def_optical_frame')
        self.publish_messages(pc, frame_marker, contact_marker, surf_pc, proc_image_msg, proc_cam_info_msg, data_dict)



    def publish_messages(self, pc, frame_marker, contact_marker, surf_pc, proc_image_msg, proc_cam_info_msg, data_dict):
        print 'publishing msgs'
        r = rospy.Rate(10.)
        while not rospy.is_shutdown():
            pc.header.stamp = rospy.get_rostime()
            frame_marker.header.stamp = rospy.get_rostime()
            contact_marker.header.stamp = rospy.get_rostime()
            surf_pc.header.stamp = rospy.get_rostime()
            proc_image_msg.header.stamp = rospy.get_rostime()
            proc_cam_info_msg.header.stamp = rospy.get_rostime()

            #print 'publishing.'
            self.marker_pub.publish(frame_marker)
            self.point_cloud_pub.publish(pc)
            self.surf_pub.publish(surf_pc)
            self.proc_pub.publish(proc_image_msg)
            self.caminfo.publish(proc_cam_info_msg)
            self.contact_pub.publish(contact_marker)

            # Publish tf between point cloud and pro
            t, r = tfu.matrix_as_tf(np.linalg.inv(data_dict['start_conditions']['pro_T_bf']))
            self.tfbroadcast.sendTransform(t, r, rospy.Time.now(), '/high_def_optical_frame', "/base_footprint")

            t, r = tfu.matrix_as_tf(np.linalg.inv(data_dict['start_conditions']['map_T_bf']))
            self.tfbroadcast.sendTransform(t, r, rospy.Time.now(), '/map', "/base_footprint")

            time.sleep(.1)
        print 'done'

def image_diff_val(before_frame, after_frame):
    br = np.asarray(before_frame)
    ar = np.asarray(after_frame)
    max_sum = br.shape[0] * br.shape[1] * br.shape[2] * 255.
    sdiff = np.sum(np.abs(ar - br)) / max_sum
    return sdiff


class Imitate:

    def __init__(self):
        rospy.init_node('imitate')
        self.should_switch = False
        self.lmat0 = None
        self.rmat0 = None
        self.pressure_exceeded = False

        self.tf_listener = tf.TransformListener()
        self.robot = pr2.PR2(self.tf_listener)
        self.prosilica = rc.Prosilica('prosilica', 'streaming')
        self.wide_angle_camera = rc.ROSCamera('/wide_stereo/left/image_rect_color')
        self.laser_scanner = hpr2.LaserScanner('point_cloud_srv')
        self.cam_info = rc.ROSCameraCalibration('/prosilica/camera_info')
        rospy.loginfo('waiting for cam info message')
        self.cam_info.wait_till_msg()
        rospy.loginfo('got cam info')
        rospy.Subscriber('pressure/l_gripper_motor', pm.PressureState, self.lpress_cb)
        rospy.loginfo('finished init')


    def shutdown(self):
        if self.should_switch:
            rospy.loginfo('switching back joint controllers')
            self.robot.controller_manager.switch(['l_arm_controller', 'r_arm_controller'], ['l_cart', 'r_cart'])
    
    def lpress_cb(self, pmsg):
        TOUCH_THRES = 3000
        lmat = np.matrix((pmsg.l_finger_tip)).T
        rmat = np.matrix((pmsg.r_finger_tip)).T
        if self.lmat0 == None:
            self.lmat0 = lmat
            self.rmat0 = rmat
            return
    
        lmat = lmat - self.lmat0
        rmat = rmat - self.rmat0
       
        #touch detected
        if np.any(np.abs(lmat) > TOUCH_THRES) or np.any(np.abs(rmat) > TOUCH_THRES):
            rospy.loginfo('Pressure limit exceedeD!! %d %d' % (np.max(np.abs(lmat)), np.max(np.abs(rmat))))
            self.pressure_exceeded = True

    def manipulate_cartesian_behavior(self, data, bf_T_obj, offset=np.matrix([.01,0,-.01]).T):
        rospy.loginfo('STATE manipulate')
        rospy.loginfo('there are %d states' % len(data['movement_states']))
        rospy.loginfo('switching controllers')
        #pdb.set_trace()
        self.robot.controller_manager.switch(['l_cart', 'r_cart'], ['l_arm_controller', 'r_arm_controller'])
        self.should_switch = True
        rospy.on_shutdown(self.shutdown)
        self.robot.left_arm.set_posture(self.robot.left_arm.POSTURES['elbowupl'])
        self.robot.right_arm.set_posture(self.robot.right_arm.POSTURES['elbowupr'])

        #rospy.loginfo('Ready to start.  Press <enter> to continue.')
        #raw_input()
        ## For each contact state
        for state in range(len(data['movement_states'])):
            if rospy.is_shutdown():
                break

            if self.pressure_exceeded:
                rospy.loginfo('Exiting movement state loop')
                break

            cur_state = data['movement_states'][state]
            rospy.loginfo("starting %s" % cur_state['name'])
            start_time = cur_state['start_time']
            wall_start_time = rospy.get_rostime().to_time()

            tl_T_bf = tfu.transform('/torso_lift_link', '/base_footprint', self.tf_listener)
            offset_mat = np.row_stack(( np.column_stack((np.matrix(np.eye(3)), offset)), np.matrix([0,0,0,1.])))
            tl_T_obj = tl_T_bf * bf_T_obj * offset_mat 
            left_tip_poses = []
            # for each joint state message
            for d in cur_state['joint_states']:
                # watch out for shut down and pressure exceeded signals
                if rospy.is_shutdown():
                    break
                if self.pressure_exceeded:
                    rospy.loginfo('Exiting inner movement state loop')
                    break

                # sleep until the time when we should send this message
                msg_time = d['time']
                msg_time_from_start = msg_time - start_time
                cur_time = rospy.get_rostime().to_time()
                wall_time_from_start = (cur_time - wall_start_time)
                sleep_time = (msg_time_from_start - wall_time_from_start) - .005
                if sleep_time > 0:
                    time.sleep(sleep_time)

                # send cartesian command
                rtip_pose_bf = tfu.matrix_as_tf((tl_T_obj * tfu.tf_as_matrix(d['rtip_obj'])))
                ltip_pose_bf = tfu.matrix_as_tf((tl_T_obj * tfu.tf_as_matrix(d['ltip_obj'])))
                self.robot.left_arm.set_cart_pose(ltip_pose_bf[0], ltip_pose_bf[1], 
                        'torso_lift_link', rospy.Time.now().to_time())
                self.robot.right_arm.set_cart_pose(rtip_pose_bf[0], rtip_pose_bf[1], 
                        'torso_lift_link', rospy.Time.now().to_time())
                left_tip_poses.append(ltip_pose_bf[0])

            rospy.loginfo("%s FINISHED" % cur_state['name'])
        #time.sleep(5)
        self.robot.controller_manager.switch(['l_arm_controller', 'r_arm_controller'], ['l_cart', 'r_cart'])
        self.should_switch = False
        rospy.loginfo('done manipulate')

    def find_pose_behavior(self, data):
        j0_dict = data['robot_pose']
        cpos = self.robot.pose()
        self.robot.head.set_poses(np.column_stack([cpos['head_traj'], j0_dict['poses']['head_traj']]), np.array([.01, 5.]))
        self.robot.torso.set_pose(j0_dict['poses']['torso'][0,0], block=True)
        #time.sleep(2)

        #rospy.loginfo('Ready to start find_pose_behavior.  Press <enter> to continue.')
        #raw_input()
        # acquire sensor data
        online = True
        rospy.loginfo('Getting high res image')
        #pdb.set_trace()
        if online: 
            image  = self.prosilica.get_frame()
        rospy.loginfo('Getting a laser scan')
        if online:
            points = None
            while points == None:
                points = self.laser_scanner.scan(math.radians(180.), math.radians(-180.), 10.)
                print points.header
                if len(points.points) < 400:
                    rospy.loginfo('Got point cloud with only %d points expected at least 400 points' % len(points.points))
                    points = None
                else:
                    rospy.loginfo('Got %d points point cloud' % len(points.points))
            #rospy.loginfo('saving pickles')
            #ut.save_pickle(points, 'tmp_points.pkl')
            #cv.SaveImage('tmp_light_switch.png', image)
        if not online:
            points = ut.load_pickle('tmp_points.pkl')
            image = cv.LoadImage('tmp_light_switch.png')
        
        # find pose
        rospy.loginfo('Finding object pose')
        #cam_info = rc.ROSCameraCalibration('/prosilica/camera_info')
        #rospy.loginfo('waiting for cam info message')
        #cam_info.wait_till_msg()
        pro_T_bf = tfu.transform('/high_def_optical_frame', '/base_footprint', self.tf_listener)
        bf_R_obj, center_bf, display_dict = find_object_pose(image, points, data, pro_T_bf, self.cam_info)
        bf_T_obj = htf.composeHomogeneousTransform(bf_R_obj, center_bf)

        ## get pose of tip of arm in frame of object (for display)
        rospy.loginfo('get pose of tip of arm in frame of object')
        rtip_objs = []
        ltip_objs = []
        rtip_pose_bf = []
        ltip_pose_bf = []
        joint_states_time = []
        for state in range(len(data['movement_states'])):
            cur_state = data['movement_states'][state]
            for d in cur_state['joint_states']:
                rtip_objs.append(d['rtip_obj'][0])
                ltip_objs.append(d['ltip_obj'][0])
                rtip_pose_bf.append(bf_T_obj * tfu.tf_as_matrix(d['rtip_obj']))
                ltip_pose_bf.append(bf_T_obj * tfu.tf_as_matrix(d['ltip_obj']))
                joint_states_time.append(d['time'])

        l_tip_objs_bf = tfu.transform_points(bf_T_obj, np.column_stack(ltip_objs))
        r_tip_objs_bf = tfu.transform_points(bf_T_obj, np.column_stack(rtip_objs))

        ## Move base!
        dframe_bf = data['start_conditions']['pose_parameters']['frame_bf']
        dcenter_bf = data['start_conditions']['pose_parameters']['center_bf']
        probot_obj = np.linalg.inv(htf.composeHomogeneousTransform(dframe_bf, dcenter_bf))
        probot_bf = tfu.matrix_as_tf(bf_T_obj * probot_obj)
        probot_bf = [probot_bf[0], tr.quaternion_from_euler(0, 0, tr.euler_from_matrix(tr.quaternion_matrix(probot_bf[1]), 'sxyz')[2], 'sxyz')]
        rospy.loginfo('Driving to location %s at %.3f m away from current pose.' % \
                (str(probot_obj[0]), np.linalg.norm(probot_bf[0])))

        ## display results
        rospy.loginfo('Sending out perception results (5 seconds).')
        #pdb.set_trace()
        display = DisplayRecordedPoseReduced(True, 5)
        display.display(bf_R_obj, center_bf, points, display_dict['surf_loc3d_pro'], image, display_dict['expected_positions3d_bf'], pro_T_bf, l_tip_objs_bf, r_tip_objs_bf, tfu.matrix_as_tf(bf_T_obj * np.linalg.inv(htf.composeHomogeneousTransform(dframe_bf, dcenter_bf))))
        return probot_bf, bf_T_obj

        #rospy.loginfo('Ready to start fine positioning.  Press <enter> to continue.')
        ## Need a refinement step
        #self.robot.base.set_pose(probot_bf[0], probot_bf[1], '/base_footprint', block=True)
        #return bf_T_obj

    def camera_change_detect(self, f, args):
        #take before sensor snapshot
        start_pose = self.robot.head.pose()
        self.robot.head.set_pose(np.radians(np.matrix([1.04, -20]).T), 1)
        time.sleep(3)
        for i in range(10):
            before_frame = self.wide_angle_camera.get_frame()
        cv.SaveImage('before.png', before_frame)
        f(*args)
        for i in range(10):
            after_frame = self.wide_angle_camera.get_frame()

        cv.SaveImage('after.png', after_frame)
        sdiff = image_diff_val(before_frame, after_frame)
        #pdb.set_trace()
        self.robot.head.set_pose(start_pose, 1)
        time.sleep(3)        
        #take after snapshot
        threshold = .7
        rospy.loginfo('camera difference %.3f' % sdiff)
        if sdiff > threshold:
            rospy.loginfo('difference detected!')
            return True
        else:
            rospy.loginfo('NO differences detected!')
            return False

    def change_detect(self, f, args):
        detectors = [self.camera_change_detect]
        results = []
        for d in detectors:
            results.append(d(f, args))
        return np.any(results)

    def pose_robot_behavior(self, data):
        j0_dict = data['robot_pose']
        pdb.set_trace()
        self.robot.left_arm.set_pose(j0_dict['poses']['larm'], 5., block=False)
        self.robot.right_arm.set_pose(j0_dict['poses']['rarm'], 5., block=False)
        self.robot.head.set_pose(j0_dict['poses']['head_traj'], 5.)
        self.robot.torso.set_pose(j0_dict['poses']['torso'][0,0], block=True)

    def run_explore(self, data_fname):
        data = ut.load_pickle(data_fname)
        self.coarse_drive_behavior(data)
        self.pose_robot_behavior(data)
        pdb.set_trace()
        probot_bf, bf_T_obj = self.find_pose_behavior(data)
        self.fine_drive_behavior(tfu.tf_as_matrix(probot_bf))

        mean = np.matrix([[0.0, 0.0, 0.0]]).T
        cov = np.matrix(np.eye(3) * .0002)
        cov[2,2] = .000005
        g = pb.Gaussian(mean, cov)

        success = False
        #pdb.set_trace()
        failed_offsets = []
        offsets = [np.matrix(np.zeros((3,1)))]
        while (not rospy.is_shutdown()) and (not success):
            rospy.loginfo('=============================================')
            rospy.loginfo('Current offset is %s' % str(offsets[0].T))
            if self.change_detect(self.manipulate_cartesian_behavior, [data, bf_T_obj, offsets[0]]):
                rospy.loginfo('success!')
                break
            else:
                failed_offsets.append(offsets[0])
            offsets[0] = np.matrix(np.zeros((3,1))) + g.sample()

        ut.save_pickle(failed_offsets, 'failed_offsets.pkl')
        ut.save_pickle([offsets[0]], 'successful_offset.pkl')

    def fine_drive_behavior(self, probot_bf):
        map_T_bf = tfu.transform('map', 'base_footprint', self.tf_listener)
        probot_map = map_T_bf * probot_bf
        #pdb.set_trace()
        self.drive_ff(tfu.matrix_as_tf(probot_map))

        #self.robot.base.move_to(np.matrix(probot_bf[0:2,3]), True)
        #current_ang_bf = tr.euler_from_matrix(tfu.transform('odom_combined', 'base_footprint', self.tf_listener)[0:3, 0:3], 'sxyz')[2]
        #odom_T_bf = tfu.transform('odom_combined', 'base_footprint', self.tf_listener)
        #ang_odom = tr.euler_from_matrix((odom_T_bf * probot_bf)[0:3, 0:3], 'sxyz')[2]
        #self.robot.base.turn_to(ang_odom, True)

    def coarse_drive_behavior(self, data):
        t, r = data['base_pose']
        rospy.loginfo('Driving to location %s' % str(t))
        rospy.loginfo('press <enter> to continue')
        raw_input()

        rvalue = self.robot.base.set_pose(t, r, '/map', block=True)
        rospy.loginfo('result is %s' % str(rvalue))
        tfinal, rfinal = self.robot.base.get_pose()
        rospy.loginfo('final pose error (step 1): %.3f' % (np.linalg.norm(t - tfinal)))
        self.drive_ff(data['base_pose'])

        #bf_T_map = tfu.transform('base_footprint', 'map', self.tf_listener)
        #p_bf = bf_T_map * tfu.tf_as_matrix(data['base_pose'])
        #self.robot.base.move_to(p_bf[0:2,3], True)

        #current_ang_map = tr.euler_from_matrix(tfu.transform('map', 'base_footprint', self.tf_listener)[0:3, 0:3], 'sxyz')[2]
        #desired_ang_map = tr.euler_from_matrix(tfu.tf_as_matrix(data['base_pose']), 'sxyz')[2]
        #delta_angle_map = desired_ang_map - current_ang_map
        #self.robot.base.turn_by(delta_angle_map)

    def drive_ff(self, tf_pose_map):
        bf_T_map = tfu.transform('base_footprint', 'map', self.tf_listener)
        p_bf = bf_T_map * tfu.tf_as_matrix(tf_pose_map)
        self.robot.base.move_to(p_bf[0:2,3], True)

        #Turn
        current_ang_map = tr.euler_from_matrix(tfu.transform('map', 'base_footprint', self.tf_listener)[0:3, 0:3], 'sxyz')[2]
        desired_ang_map = tr.euler_from_matrix(tfu.tf_as_matrix(tf_pose_map), 'sxyz')[2]
        delta_angle_map = desired_ang_map - current_ang_map
        self.robot.base.turn_by(delta_angle_map)

    def run(self, data_fname, state='fine_positioning'):
        ##                                                                   
        # Data dict
        # ['camera_info', 'map_T_bf', 'pro_T_bf', 'points' (in base_frame), 
        # 'highdef_image', 'model_image', 
        #  'pose_parameters']
            #  'descriptors'
            #  'directions' (wrt to cannonical orientation)
            #  'closest_feature'
            #  'object_frame'

        data = ut.load_pickle(data_fname)
    
        ##Need to be localized!!
        ## NOT LEARNED: go into safe state.
        
        ## coarse_driving. learned locations. (might learn path/driving too?)
        if state == 'coarse_driving':
            t, r = data['base_pose']
            rospy.loginfo('Driving to location %s' % str(t))
            rospy.loginfo('Ready to start driving.  Press <enter> to continue.')
            raw_input()
            rvalue = self.robot.base.set_pose(t, r, '/map', block=True)
            rospy.loginfo('result is %s' % str(rvalue))
            state = 'start_pose'

            tfinal, rfinal = self.robot.base.get_pose()
            print 'Final pose error: %.3f' % (np.linalg.norm(t - tfinal))
            state = 'start_pose'

        if state == 'start_pose':
            rospy.loginfo('Ready to start start_pose.  Press <enter> to continue.')
            self.pose_robot_behavior(data)
            state = 'fine_positioning'

        if state == 'fine_positioning':
            bf_T_obj = self.find_pose_behavior(data)
            state = 'start_pose'

        ## Move joints to initial state. learned initial state. (maybe coordinate this with sensors?)
        #Put robot in the correct state

        if state == 'manipulate_cart2':
            self.manipulate_cartesian_behavior(data, bf_T_obj)

        if state == 'fine_positioning_offline':
            rospy.loginfo('Finding object pose (offline)')

            pro_T_bf = data['start_conditions']['pro_T_bf']
            cam_info = data['start_conditions']['camera_info']
            image = cv.LoadImage(data['start_conditions']['model_image'])
            points = data['start_conditions']['points']

            bf_R_obj, center_bf, display_dict = find_object_pose(image, points, data, pro_T_bf, cam_info)
            print 'DONE!'

            ################################################################
            ## get pose of tip of arm in frame of object
            rospy.loginfo('get pose of tip of arm in frame of object')
            rtip_objs = []
            ltip_objs = []
            rtip_objs_bf = []
            ltip_objs_bf = []
            for state in range(len(data['movement_states'])):
                cur_state = data['movement_states'][state]
                for d in cur_state['joint_states']:
                    rtip_objs.append(d['rtip_obj'][0])
                    ltip_objs.append(d['ltip_obj'][0])
                    rtip_objs_bf.append(d['rtip_bf'][0])
                    ltip_objs_bf.append(d['ltip_bf'][0])
            rtip_objs_obj = np.column_stack(rtip_objs)
            ltip_objs_obj = np.column_stack(ltip_objs)

            rtip_objs_bf = np.column_stack(rtip_objs_bf)
            ltip_objs_bf = np.column_stack(ltip_objs_bf)

            dframe_bf = data['start_conditions']['pose_parameters']['frame_bf']
            dcenter_bf = data['start_conditions']['pose_parameters']['center_bf']
            bf_T_obj = htf.composeHomogeneousTransform(dframe_bf, dcenter_bf)
            probot_obj = np.linalg.inv(htf.composeHomogeneousTransform(dframe_bf, dcenter_bf))
            probot_bf = tfu.matrix_as_tf(bf_T_obj * probot_obj)
            pdb.set_trace()

            rospy.loginfo('sending out results')
            display = DisplayRecordedPoseReduced(False, 100)
            display.display(bf_R_obj, center_bf, points,
                    display_dict['surf_loc3d_pro'], image, 
                    display_dict['expected_positions3d_bf'], pro_T_bf,
                    ltip_objs_bf, rtip_objs_bf, 
                    probot_bf,
                    data['start_conditions']['pose_parameters']['contact_bf'])

    
        if state == 'manipulate_cart':
            rospy.loginfo('STATE manipulate')
            rospy.loginfo('there are %d states' % len(data['movement_states']))
            rospy.loginfo('switching controllers')
            self.robot.controller_manager.switch(['l_cart', 'r_cart'], ['l_arm_controller', 'r_arm_controller'])
            self.should_switch = True
            rospy.on_shutdown(self.shutdown)
            self.robot.left_arm.set_posture(self.robot.left_arm.POSTURES['elbowupl'])
            self.robot.right_arm.set_posture(self.robot.right_arm.POSTURES['elbowupr'])
    
            self.robot.left_arm.set_posture(self.robot.left_arm.POSTURES['elbowupl'])
            self.robot.right_arm.set_posture(self.robot.right_arm.POSTURES['elbowupr'])

            ## For each contact state
            for state in range(len(data['movement_states'])):

                if rospy.is_shutdown():
                    break

                if self.pressure_exceeded:
                    rospy.loginfo('Exiting movement state loop')
                    break
    
                cur_state = data['movement_states'][state]
                rospy.loginfo("starting %s" % cur_state['name'])
                left_cart  = cur_state['cartesian'][0]
                right_cart = cur_state['cartesian'][1]
                start_time = cur_state['start_time']
                wall_start_time = rospy.get_rostime().to_time()
    
                for ldict, rdict in zip(left_cart, right_cart):
                    if rospy.is_shutdown():
                        break
                    if self.pressure_exceeded:
                        rospy.loginfo('Exiting inner movement state loop')
                        break
                    lps = dict_to_arm_arg(ldict)
                    rps = dict_to_arm_arg(rdict)
    
                    msg_time_from_start = ((lps[3] - start_time) + (rps[3] - start_time))/2.0
                    cur_time = rospy.get_rostime().to_time()
                    wall_time_from_start = (cur_time - wall_start_time)
    
                    sleep_time = (msg_time_from_start - wall_time_from_start) - .005
                    if sleep_time < 0:
                        rospy.loginfo('sleep time < 0, %f' % sleep_time)
    
                    if sleep_time > 0:
                        time.sleep(sleep_time)
    
                    lps[3] = rospy.get_rostime().to_time()
                    rps[3] = rospy.get_rostime().to_time()
                    self.robot.left_arm.set_cart_pose(*lps)
                    self.robot.right_arm.set_cart_pose(*rps)
                #rospy.loginfo("%s FINISHED" % cur_state['name'])
                #time.sleep(5)
    
            self.robot.controller_manager.switch(['l_arm_controller', 'r_arm_controller'], ['l_cart', 'r_cart'])
            self.should_switch = False
        
        if state == 'manipulate':
            rospy.loginfo('STATE manipulate')
            rospy.loginfo('there are %d states' % len(data['movement_states']))
            ## For each contact state
            for state in range(len(data['movement_states'])):
                cur_state = data['movement_states'][state]
                rospy.loginfo("starting %s" % cur_state['name'])
        
                larm, lvel, ltime, rarm, rvel, rtime = zip(*[[jdict['poses']['larm'], jdict['vels']['larm'], jdict['time'], \
                                                              jdict['poses']['rarm'], jdict['vels']['rarm'], jdict['time']] \
                                                                    for jdict in cur_state['joint_states']])
        
                larm = np.column_stack(larm)
                lvel = np.column_stack(lvel)
                ltime = np.array(ltime) - cur_state['start_time']
    
                rarm = np.column_stack(rarm)
                rvel = np.column_stack(rvel)
                rtime = np.array(rtime) - cur_state['start_time']
        
                ## Send the first pose and give the robot time to execute it.
                self.robot.left_arm.set_poses(larm[:,0], np.array([2.]), block=False)
                self.robot.right_arm.set_poses(rarm[:,0], np.array([2.]), block=True)
        
                ## Send trajectory. wait until contact state changes or traj. finished executing.
                self.robot.left_arm.set_poses(larm, ltime, vel_mat=lvel, block=False)
                self.robot.right_arm.set_poses(rarm, rtime, vel_mat=rvel, block=True)
        
                rospy.loginfo("%s FINISHED" % cur_state['name'])
                time.sleep(5)
    
        ## rosbag implementation steps in time and also figures out how long to sleep until it needs to publish next message
        ## Just play pose stamped back at 10 hz
        ## For each contact state


class ControllerTest:
    def __init__(self):
        self.robot = pr2.PR2()

    def run(self):
        rospy.loginfo('switching to cartesian controllers')
        self.robot.controller_manager.switch(['l_cart', 'r_cart'], ['l_arm_controller', 'r_arm_controller'])
        rospy.on_shutdown(self.shutdown)
        r = rospy.Rate(1)
        #publish posture & cartesian poses
        while not rospy.is_shutdown():
            self.robot.left_arm.set_posture(self.robot.left_arm.POSTURES['elbowupl'])
            self.robot.right_arm.set_posture(self.robot.right_arm.POSTURES['elbowupr'])
            r.sleep()

    def shutdown(self):
        rospy.loginfo('switching back joint controllers')
        self.robot.controller_manager.switch(['l_arm_controller', 'r_arm_controller'], ['l_cart', 'r_cart'])

    
if __name__ == '__main__':

    if True:
        #prosilica = rc.Prosilica('prosilica', 'streaming')
        #pdb.set_trace()
        #f = prosilica.get_frame()
        #print f
        im = Imitate()
        im.run_explore(sys.argv[1])

    if False:
        rospy.init_node('send_base_cmd')
        client = actionlib.SimpleActionClient('go_angle', hm.GoAngleAction)
        #client.wait_for_server()
        pdb.set_trace()

        goal = hm.GoAngleGoal()
        goal.angle = math.radians(90)
        print 'sending goal'
        client.send_goal(goal)
        print 'waiting'
        client.wait_for_result()

    if False:
        rospy.init_node('send_base_cmd')
        client = actionlib.SimpleActionClient('go_xy', hm.GoXYAction)
        client.wait_for_server()
        pdb.set_trace()

        goal = hm.GoXYGoal()
        goal.x = .2
        print 'sending goal'
        client.send_goal(goal)
        print 'waiting'
        client.wait_for_result()


    if False:
        dd = DisplayRecordedPose()
        dd.display_original_object_pose(sys.argv[1])

    if False:
        c = ControllerTest()
        c.run()













        # Publish 3D points (in high_def_optical_frame)
        #point_cloud_bf = ru.pointcloud_to_np(data_dict['start_conditions']['points'])
        #point_cloud_pro = (data_dict['start_conditions']['pro_T_bf'] * \
        #                    np.row_stack((point_cloud_bf, 1+np.zeros((1, point_cloud_bf.shape[1])))))[0:3,:]
        # point_cloud_kd_pro = sp.KDTree(point_cloud_pro.T)

        #center_pro = np.mean(np.matrix(surf_loc3d_pro), 1)
        #neighbor_idxs = point_cloud_kd_pro.query_ball_point(np.array(center_pro.T), .1)
        #points_nearby_pro = point_cloud_pro[:, neighbor_idxs[0]]
        # points_nearby_pro_pc = ru.np_to_pointcloud(points_nearby_pro, 'high_def_optical_frame')
        #print 'point_cloud_pro.shape', point_cloud_pro.shape

        # create frame
        #import hai_sandbox.bag_processor as bp
        #pdb.set_trace()
        #surf_loc3d_bf = (np.linalg.inv(data_dict['start_conditions']['pro_T_bf']) \
        #        * np.row_stack((surf_loc3d_pro, 1+np.zeros((1,surf_loc3d_pro.shape[1])))))[0:3,:]
        #frame_bf = bp.create_frame(surf_loc3d_bf, p=np.matrix([-1, 0, 0.]).T)
        #center_bf = np.mean(surf_loc3d_bf, 1)

        ########################################################
        # Publish frame as a line list
        #pdb.set_trace()
        #center_pro, frame_pro, surf_loc_3d_pro = data_dict['start_conditions']['pose_parameters']['object_frame_pro']
        #center_pro = np.matrix(np.zeros((3,1)))
        #frame_pro = np.matrix(np.eye(3))


        ########################################################
        #import hai_sandbox.bag_processor as bp
        #point_cloud_pro = (data_dict['start_conditions']['pro_T_bf'] * np.row_stack((point_cloud_bf, 1+np.zeros((1, point_cloud_bf.shape[1])))))[0:3,:]
        #cam_info = data_dict['start_conditions']['camera_info']
        #Project into 2d
        #point_cloud_2d_pro = data_dict['start_conditions']['camera_info'].project(point_cloud_pro) 
        #only count points in bounds (should be in cam info)
        #_, in_bounds = np.where(np.invert((point_cloud_2d_pro[0,:] >= (cam_info.w-.6)) + (point_cloud_2d_pro[0,:] < 0) \
        #                                + (point_cloud_2d_pro[1,:] >= (cam_info.h-.6)) + (point_cloud_2d_pro[1,:] < 0)))
        #point_cloud_2d_pro = point_cloud_2d_pro[:, in_bounds.A1]
        #point_cloud_reduced_pro = point_cloud_pro[:, in_bounds.A1]

        # Find SURF features
        #model_surf_loc, model_surf_descriptors = fea.surf_color(cv.LoadImage(model_file_name))
        #surf_loc3d_pro = np.matrix(bp.assign_3d_to_surf(model_surf_loc, point_cloud_reduced_pro, point_cloud_2d_pro))
        #point_cloud_pro = data_dict['start_conditions']['point_cloud_pro']

        # point_cloud_bf = ru.pointcloud_to_np(data_dict['start_conditions']['points'])





        #    data = {'base_pose': pose_base, 
        #            'robot_pose': j0_dict,
        #            'arm': arm_used,
        #            'movement_states': None}
        #rospy.Subscriber('/pressure/r_gripper_motor', pm.PressureState, self.lpress_cb)
        #self.pr2_pub = rospy.Publisher(pr2_control_topic, PoseStamped)
        #global robot
        #global should_switch
        #global pressure_exceeded
