from numpy.core.fromnumeric import nonzero
from numpy.lib import index_tricks
from lib_grid_map import GridMap
import numpy as np


class ProbMap(GridMap):
    """[summary]

    Args:
        Input msg:  uint32 class_id
                    string class_name
                    float32 confidence *

                    # ground truth unique identifier and position of the detected object (Simulation only) 
                    uint32 object_id *
                    float32 x_pos *
                    float32 y_pos *
                    float32 z_pos *

                    # ground truth kpts projection
                    Keypoint[] kpts

                    https://gitlab.sitcore.net/arl/robotics-simulation/arl-unity-ros/-/blob/master/arl_unity_ros/msg/Detection.msg
    """

    def __init__(self, width_meter, height_meter, resolution,
                 center_x, center_y, init_val=0.01, false_alarm_prob=0.05):
        GridMap.__init__(self, width_meter, height_meter, resolution,
                         center_x, center_y, init_val)
        self.non_empty_cell = dict()
        self.false_alarm_prob = false_alarm_prob

    def set_value_from_xy_index(self, index, val):
        """Stores the value in grid map

        Args:
            index (tuple): 2D tuple represents x, y coordinates.
            val (float): Value need to be stored.
        """
        # If Q value after update is small enough to make the probability be zero,
        # it's safe to delete the cell for a better memory usage
        if val == 700.0:
            self.delete_value_from_xy_index(index)
        else:
            self.non_empty_cell[index] = val

    def get_value_from_xy_index(self, index):
        """Get the value from given cell

        Args:
            index (tuple): 2D tuple represents x, y coordinates.

        Returns:
            [float]: Value from the given cell
        """
        return self.non_empty_cell[index]

    def delete_value_from_xy_index(self, index):
        """Delete the item from grid map

        Args:
            index (tuple): 2D tuple represents x, y coordinates.
        """
        try:
            del self.non_empty_cell[index]
        except KeyError:
            pass

    def generate_shareable_v(self, local_measurement):
        meas_index = dict()
        for _target_id, meas in local_measurement.items():
            x_pos, y_pos, meas_confidence = meas
            point_ind = tuple(self.get_xy_index_from_xy_pos(x_pos, y_pos))
            # meas_index[point_ind] = meas_confidence
            meas_index[point_ind] = np.log(
                self.false_alarm_prob/meas_confidence)
        return meas_index

    def generate_zero_meas(self):
        def cut(x): return 1e-6 if x <= 1e-6 else 1 - \
            1e-6 if x >= 1-1e-6 else x
        meas_confidence = cut(np.random.normal(0.85, 0.1))
        x = np.log((1-self.false_alarm_prob)/(1-meas_confidence))
        return x

    def map_update(self, local_measurement, neighbor_measurement, N, d):
        """Update the probability map by measurements and generate shareable infomation if needed.

        Args:
            measurement (dict): Contains measurements like {id1:[x1, y1, confidence1], id2:[x2, y2, confidence2]}
            N (int): Number of all trackers
            d (int): Number of all neighbors
            local (bool, optional): Local update or neighbor update. Defaults to True.
        """

        DEBUG = 0
        # We don't need a huge

        def bound_Q(Q):
            # 700 is big enough to make 1/(1+exp(700)) -> 0
            return max(min(Q, 700), -700)

        # Get the weight of measurements
        weight_local = 1. - (d-1.)/N
        weight_neighbor = 1./N

        # Time decaying factor
        alpha = 4
        T = 0.1
        decay_factor = np.exp(-alpha*T)

        # update all existing grids
        for cell_ind in self.non_empty_cell.keys():
            if cell_ind in local_measurement:
                v_local = local_measurement[cell_ind]
                del local_measurement[cell_ind]
            else:
                v_local = self.generate_zero_meas()

            if cell_ind in neighbor_measurement:
                v_neighbors = neighbor_measurement[cell_ind]
                del neighbor_measurement[cell_ind]
            else:
                v_neighbors = sum(
                    [self.generate_zero_meas() for i in range(d)])

            Q = weight_local*(self.non_empty_cell[cell_ind] + v_local) + weight_neighbor * (
                d*self.non_empty_cell[cell_ind]+v_neighbors)
            self.set_value_from_xy_index(cell_ind, bound_Q(decay_factor * Q))
        # If got measurement for a new cell
        else:
            # get the union set of all remaining measurements
            all_meas = set(local_measurement.keys() +
                           neighbor_measurement.keys())
            for cell_ind in all_meas:
                try:
                    v_local = local_measurement[cell_ind]
                except KeyError:
                    # print("no v_local")
                    v_local = self.generate_zero_meas()
                try:
                    v_neighbors = neighbor_measurement[cell_ind]
                except KeyError:
                    # print("no v_neigh")
                    v_neighbors = sum(
                        [self.generate_zero_meas() for i in range(d)])
                Q = weight_local*(self.init_val + v_local) + weight_neighbor * (
                    d*self.init_val+v_neighbors)
                self.set_value_from_xy_index(
                    cell_ind, bound_Q(decay_factor * Q))

    def consensus(self, neighbors_map):
        """Merge neighbors map into local map and make a consensus

        Args:
            neighbors_map (dict): Contains all values from neighbors and have a count of it. Format: {(x, y):[value, count]}
        """
        for cell_ind, value in self.non_empty_cell.items():
            if cell_ind in neighbors_map:
                # Calculate the average value of Q
                Q = (neighbors_map[cell_ind][0]+value) / \
                    (neighbors_map[cell_ind][1]+1)
                self.set_value_from_xy_index(cell_ind, Q)
                del neighbors_map[cell_ind]
        else:
            for cell_ind, value_and_count in neighbors_map.items():
                Q = value_and_count[0]/value_and_count[1]
                self.set_value_from_xy_index(cell_ind, Q)
        self.convert_to_prob_map()

    def convert_to_prob_map(self):
        self.prob_map = dict()
        for cell_ind, value in self.non_empty_cell.items():
            prob = 1./(1.+np.exp(value))
            if prob>=0.01:
                self.prob_map[cell_ind] = prob
