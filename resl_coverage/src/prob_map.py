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
        # TODO Figure out how to set this value
        self.false_alarm_prob = false_alarm_prob

    def set_value_from_xy_index(self, index, val):
        """Stores the value in grid map

        Args:
            index (tuple): 2D tuple represents x, y coordinates.
            val (float): Value need to be stored.
        """
        # If the probability after update is even smaller than initial value,
        # it's safe to delete the cell for a better memory usage
        if np.isnan(val) or val <= self.init_val:
            self.delete_value_from_xy_index(index)
            # self.delete_value_from_xy_index(index)
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

    def map_update(self, measurement):
        """Update the probability map by received measurement

        Args:
            measurement_dict (dict): Contains measurements like {id1:[x1, y1, confidence1], id2:[x2, y2, confidence2]}
        """
        meas_index = dict()
        # Transfer the measurement to a dict like {(x, y) : prob}, where x,y is the index of the grid
        for _target_id, meas in measurement.items():
            x_pos, y_pos, meas_confidence = meas
            point_ind = tuple(self.get_xy_index_from_xy_pos(x_pos, y_pos))
            meas_index[point_ind] = meas_confidence

        for cell_ind in self.non_empty_cell.keys():
            # update all existing grids
            if cell_ind in meas_index:
                meas_confidence = meas_index[cell_ind]
                old_cell_prob = self.get_value_from_xy_index(cell_ind)
                v = np.log((1-meas_confidence)/meas_confidence)
                Q = np.log(1/old_cell_prob-1)+v
                self.set_value_from_xy_index(cell_ind, Q)
                # If this measument has merged into the prob map, delete it to get the unmerged part
                # del meas_index[cell_ind]
                # For debug reasons, set the value to None
                meas_index[cell_ind] = None
            else:
                # if the existing grid doesn't have any update data means nothing there
                def cutOff(x): return 1e-6 if x <= 1e-6 else 1 - \
                    1e-6 if x >= 1-1e-6 else x
                # generate a reasonable prob for not sensing anything
                meas_confidence = cutOff(np.random.normal(0.85, 0.1))
                old_cell_prob = self.get_value_from_xy_index(cell_ind)
                v = np.log(meas_confidence/(1-meas_confidence))
                Q = np.log(1/old_cell_prob-1)+v
                self.set_value_from_xy_index(cell_ind, Q)
        # For the measuments appearing in the new cells
        for cell_ind, meas_confidence in meas_index.items():
            # Check if the value is unmerged
            if meas_confidence != None:
                meas_confidence = meas_index[cell_ind]
                # old_cell_prob = self.init_val
                # new_cell_prob = (meas_confidence*old_cell_prob)\
                #     / ((meas_confidence*old_cell_prob)+(1-meas_confidence)*(1-old_cell_prob))
                v = np.log((1-meas_confidence)/meas_confidence)
                Q = np.log(1/self.init_val-1)+v
                # self.set_value_from_xy_index(cell_ind, new_cell_prob)
                self.set_value_from_xy_index(cell_ind, Q)
        # print(self.non_empty_cell)
