#!/usr/bin/env python

import sys
import rospy
import numpy as np
import networkx as nx
from time import time
from copy import deepcopy
from detector import Detector
from numpy.linalg import pinv
from geometry_msgs.msg import PoseStamped, TwistStamped
from resl_coverage.msg import MultiStateEstimate
from resl_coverage.srv import Topology, TopologyResponse
from resl_coverage.srv import ProcessNoise, ProcessNoiseResponse
from resl_coverage.srv import Failure, FailureResponse
from resl_coverage.srv import Coordinates, CoordinatesResponse
from resl_coverage.srv import TriggerFail, TriggerFailResponse
from resl_coverage.srv import State, StateResponse
from resl_coverage.srv import Neighbors, NeighborsRequest
from prob_map import ProbMap
from resl_coverage.srv import MeasShare, MeasShareRequest, MeasShareResponse
import matplotlib.pyplot as plt
from itertools import chain

# build a grid map
#           |--size: 20m x 20m
#           |--resolution: 0.1m
#           |--center coordinates: [0.0m, 0.0m]
#           |--default grid value: 0.1
width_meter = 50
height_meter = 50
resolution = 0.2
prob_map = ProbMap(width_meter, height_meter, resolution,
                   center_x=0.0, center_y=0.0, init_val=0.01,
                   false_alarm_prob=0.05)


num_targets = int(sys.argv[1])
num_trackers = int(sys.argv[2])

myid = None
irec = {}
ob_rec = {}
information_q = {}  # Update to dictionary
information_W = {}  # Update to dictionary
edges = []

# Declare Subscribers
offset_sub = None
tracker_pose_sub = None
target_pose_subs = []
target_twist_subs = []
consensus_subs = []

obs = {i: [None, None, None, None] for i in range(num_targets)}
covariances = [np.dot(0.01, np.eye(4)) for i in range(num_targets)]
estimates = [np.array([0., 0., 0., 0.]) for i in range(num_targets)]
offset = [float(sys.argv[3]), float(sys.argv[4]), 5.]
meas = [False for i in range(num_targets)]

set_est = [[False, False] for i in range(num_targets)]

A = None
B = None
H = None
U = None
Q = None
R = None

# Shareable information
all_meas_info_service = []
# meas_information = None
local_meas_info_res = MeasShareResponse()
neighbors_meas = dict()


def pose_callback(msg, args):
    # msg[PoseStamped]:
    #  |--std_msgs/Header header
    #  |--geometry_msgs/Pose pose
    global obs, estimates, set_est, R
    noise = np.dot(R, np.random.random(4))
    obs[args][0] = msg.pose.position.x + (noise[0] * 0.01)
    obs[args][1] = msg.pose.position.y + (noise[1] * 0.01)
    if not set_est[args][0]:
        estimates[args][0] = msg.pose.position.x
        estimates[args][1] = msg.pose.position.y
        set_est[args][0] = True


def offset_callback(msg):
    global offset
    offset[0] = msg.pose.position.x
    offset[1] = msg.pose.position.y
    offset[2] = msg.pose.position.z


def tracker_pose_callback(msg):
    global tracker_pose
    p = msg.pose.position
    tracker_pose = [p.x, p.y, p.z]


def information_callback(msg):
    global irec, information_q, information_W, ob_rec
    global num_targets
    information_q[msg.id] = np.array(msg.q).reshape((num_targets, 4))
    information_W[msg.id] = np.array(msg.W).reshape((num_targets, 4, 4))
    irec[msg.id] = True
    ob_rec[msg.id] = msg.z_rec


def handle_topology(req):
    global edges, weight_matrix, N
    global num_trackers
    global irec, myid, name
    global neighbors_service, neighbors_req

    edges = req.edges
    for e in edges:
        irec[e] = False

    rospy.wait_for_service(name+'neighbors')
    #neighbors_req.neighbors = edges
    neighbors_req.neighbors = [i for i in range(num_trackers) if i != myid]
    neighbors_service(neighbors_req)

    N = len(edges)
    weight_matrix = np.array(req.weight_matrix).reshape(
        (num_trackers, num_trackers))

    global consensus_subs
    for j in range(num_trackers):
        if j != myid and j in edges:
            consensus_subs.append(
                rospy.Subscriber('/tracker'+str(j)+'/state_information',
                                 MultiStateEstimate, information_callback))

    res = TopologyResponse()
    res.rec = 1
    return res


def handle_offsets(req):
    global offset
    offset[0] = req.x
    offset[1] = req.y
    offset[2] = req.z

    res = CoordinatesResponse()
    res.rec = 1
    return res

# XXX what failure?


def handle_failure(req):
    global estimates, covariances
    global consensus_subs, edges
    edges = []
    consensus_subs = []
    irec = {}

    res = FailureResponse()
    inter_W = np.array([pinv(covariances[i]) for i in range(num_targets)])
    res.W = inter_W.flatten()
    res.x = np.array(estimates).flatten()
    res.P = np.array(covariances).flatten()
    return res


def handle_process_noise(req):
    global R
    return ProcessNoiseResponse(R.flatten())


def handle_trigger_fail(req):
    global R
    R = np.dot(1.1, R)
    res = TriggerFailResponse()
    return res


def handle_state_request(req):
    global estimates, meas
    res = StateResponse()
    res.state = np.array(estimates).flatten()
    res.measured = meas
    return res


def handle_share_meas(req):
    return local_meas_info_res


def get_all_neighbors_meas():
    global myid, neighbors_meas
    neighbors_meas = dict()
    all_meas = []
    for s in all_meas_info_service:
        all_meas.append(s(myid))

    for res in all_meas:
        for i in range(len(res.values)):
            cell_ind = tuple([res.grid_ind[i*2], res.grid_ind[i*2+1]])
            value = res.values[i]
            # sum all neighbors' values
            try:
                neighbors_meas[cell_ind] += value
            except KeyError:
                neighbors_meas[cell_ind] = value


def init_params():
    global name, myid
    global num_targets
    global A, B, U, Q, H, R

    name = rospy.get_namespace()
    myid = int(name[1:-1].replace('tracker', ''))

    A = np.eye(4)
    B = np.concatenate((np.zeros((2, 2)), np.eye(2)))
    U = [np.ones(2) for i in range(num_targets)]
    Q = np.eye(4)
    H = np.eye(4)
    R = np.eye(4)


def init_messages():
    global name
    global num_targets, num_trackers

    # Publication
    global state_information, desired_pose
    global des_pub, information_pub

    state_information = MultiStateEstimate()
    desired_pose = PoseStamped()

    des_pub = rospy.Publisher(name+'desired_pose', PoseStamped, queue_size=1)
    information_pub = rospy.Publisher(name+'state_information',
                                      MultiStateEstimate, queue_size=2*num_trackers)

    # Subscription
    global offset_sub, target_pose_subs
    global target_twist_subs, tracker_pose_sub
    offset_sub = rospy.Subscriber(name+'offset', PoseStamped, offset_callback)
    tracker_pose_sub = rospy.Subscriber('/unity_command'+name+'TrueState/pose',
                                        PoseStamped, tracker_pose_callback)
    for i in range(num_targets):
        target_pose_subs.append(
            rospy.Subscriber('/unity_command/target'+str(i)+'/TrueState/pose',
                             PoseStamped, pose_callback, i))
        # target_twist_subs.append(
        #     rospy.Subscriber('/unity_command/target'+str(i)+'/TrueState/twist',
        #                      TwistStamped, twist_callback, i))


def init_services():
    global name
    global process_noise_res, failure_res
    global des_offset_res, topology_res
    global trigger_fail_res, state_res
    global neighbors_service, neighbors_req

    topology_res = rospy.Service(name+'topology', Topology, handle_topology)
    des_offset_res = rospy.Service(
        name+'desired_offsets', Coordinates, handle_offsets)
    process_noise_res = rospy.Service(
        name+'process_noise', ProcessNoise, handle_process_noise)
    failure_res = rospy.Service(name+'failure', Failure, handle_failure)
    trigger_fail_res = rospy.Service(
        name+'trigger_fail', TriggerFail, handle_trigger_fail)
    state_res = rospy.Service(name+'state_estimate',
                              State, handle_state_request)

    rospy.wait_for_service(name+'neighbors')
    neighbors_req = NeighborsRequest()
    neighbors_service = rospy.ServiceProxy(name+'neighbors', Neighbors)

    # Service that shares the local measurements
    meas_info_res = rospy.Service(
        name+'meas_info', MeasShare, handle_share_meas)


def init_meas_services():
    global edges, name
    rospy.logdebug(name+"MeasServices initializing", edges)
    for i in edges:
        rospy.logdebug("checking measurement service of Tracker"+str(i))
        rospy.wait_for_service('/tracker{}/'.format(i)+'meas_info')
        all_meas_info_service.append(rospy.ServiceProxy(
            name+'meas_info', MeasShare))


def build_shareable_v(shareable_v):
    global myid, local_meas_info_res

    local_meas_info_res = MeasShareResponse()
    local_meas_info_res.tracker_id = myid
    for k, v in shareable_v.items():
        local_meas_info_res.grid_ind += k
        local_meas_info_res.values.append(v)


def track(plot_map=0):
    global num_targets, num_trackers, myid, name
    global irec, q, W, information_q, information_W
    global obs, offset, estimates, covariances
    global A, B, U, Q, H, R
    global N, edges, node_weights, ob_rec, meas
    global desired_pose, des_pub
    global information_pub, state_information
    global tracker_pose
    global prob_map, neighbors_meas
    global local_meas_info_res

    print(name+"start tracking...")

    if plot_map:
        # Plot the dynamic prob map
        plt.ion()
        plt.plot()

    rospy.init_node(name[1:-1] + '_tracking')

    print(name+"node initialized")

    detector = Detector(3.141592654 / 4.)

    rospy.sleep(5)
    rate = rospy.Rate(10)
    init_meas_services()
    while not rospy.is_shutdown():
        while not edges:
            rospy.logdebug(name+" passing 'cuz no edges")
            pass

        if all([ob[0] for k, ob in obs.items()]):
            obs, z_rec = detector.get_detections(
                tracker_pose, obs, get_all=False, pr=False, prob=True)

            # build a real_obs dict which doesn't contain unvisible targets
            real_obs = dict()
            for i in range(len(z_rec)):
                if z_rec[i]:
                    real_obs[i] = obs[i]

            # use local real observation data to update the prob map
            shareable_v = prob_map.map_update_local_info(real_obs)

            # build the response for shareable_v
            build_shareable_v(shareable_v)
            # rospy.logdebug(name+"local meas",local_meas_info_res)

            # get all neighbors' measurements
            # XXX maybe should not use global variables
            get_all_neighbors_meas()
            # rospy.logdebug(name+"Neighbors meas",neighbors_meas)

            shareable_H = prob_map.map_update_neighbor_info(neighbors_meas)
            # NOTE I think it's nessecery to restore the neighbors' measurement
            neighbors_meas = dict()
            
            # TODO do the same thing as above to get H from neighbors and merge it into map
            build_shareable_H()
            get_all_neighbors_H()
            prob_map.map_fuse_neighbor_info()

            #####################
            # Plot the Prob map #
            #####################
            if plot_map:
                plt.clf()
                for ind, value in prob_map.non_empty_cell.items():
                    plt.scatter(ind[0], ind[1], s=round(
                        np.arctan(value)*20, 2), c='r')
                    # plt.annotate(round(value, 2), ind)
                    # if value>=0.5:
                    #     plt.annotate(round(value,2), ind)
                plt.axis([0, prob_map.width, 0, prob_map.height], "equal")
                plt.title("Tracker"+str(myid))
                plt.grid(alpha=0.1)
                # regenerate the labels of x, y
                plt.xticks(np.arange(0, width_meter/resolution, 5/resolution),
                           np.arange(-width_meter/2, width_meter/2, 5))
                plt.yticks(np.arange(0, height_meter/resolution, 5/resolution),
                           np.arange(-height_meter/2, height_meter/2, 5))
                plt.draw()
                plt.pause(0.01)

            # Move trackers to the position
            desired_pose.pose.position.x = offset[0]
            desired_pose.pose.position.y = offset[1]
            desired_pose.pose.position.z = offset[2]
            des_pub.publish(desired_pose)

        rate.sleep()


if __name__ == "__main__":
    try:
        init_params()
        init_services()
        init_messages()
        track(plot_map=0)
    except rospy.ROSInterruptException:
        pass
