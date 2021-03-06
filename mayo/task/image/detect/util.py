import tensorflow as tf
import numpy as np

from mayo.util import ShapeError


def box_to_corners(box, unstack=True, stack=True):
    if unstack:
        box = tf.unstack(box, axis=-1)
    x, y, w, h = box
    w_half, h_half = w / 2, h / 2
    corners = [y - h_half, x - w_half, y + h_half, x + w_half]
    if stack:
        return tf.stack(corners, axis=-1)
    return corners


def corners_to_box(corners, unstack=True, stack=True):
    if unstack:
        corners = tf.unstack(corners, axis=-1)
    y_min, x_min, y_max, x_max = corners
    y = (y_min + y_max) / 2
    x = (x_min + x_max) / 2
    h = y_max - y_min
    w = x_max - x_min
    box = [x, y, w, h]
    if stack:
        return tf.stack(box, axis=-1)
    return box


def area(y_min, x_min, y_max, x_max):
    return (y_max - y_min) * (x_max - x_min)


def shape(tensor):
    shape_values = list(tensor.shape)
    shape_tensors = tf.unstack(tf.shape(tensor))
    for i, s in enumerate(shape_values):
        if s.value is None:
            shape_values[i] = shape_tensors[i]
        else:
            shape_values[i] = int(s)
    return shape_values


def cartesian(tensor1, tensor2):
    """
    Computes the pair-wise combinations of first dimensions of
    tensor1 and tensor2, leaving the last dimension.
    """
    # exclude last dimension
    *shape1, last1 = shape(tensor1)
    *shape2, last2 = shape(tensor2)
    if last1 != last2:
        raise ShapeError(
            'The last dimension of both tensors should be constant '
            'and should match.')
    ndims1 = len(shape1)
    ndims2 = len(shape2)
    # num_rows1 x num_row2 x expected_size tensors to form
    # all possible bbox pairs
    reshaped1 = tf.reshape(tensor1, shape1 + [1] * ndims2 + [last1])
    reshaped2 = tf.reshape(tensor2, [1] * ndims1 + shape2 + [last2])
    tensor1 = tf.tile(reshaped1, [1] * ndims1 + shape2 + [1])
    tensor2 = tf.tile(reshaped2, shape1 + [1] * ndims2 + [1])
    return tensor1, tensor2


def iou(boxes1, boxes2, anchors=False):
    """
    Compute IOU values from two tensors of bounding boxes.

    boxes1, boxes2:
        a (... x 4) tensor, where the last dimension contains the
        coordinates (x, y, w, h), respectively denote the center of the
        box and its height and width.
    anchors:
        boxes1 and 2 to be (... x 2) tensors containing only
        heights and widths, treating the origin as the centers.

    returns: a (...) tensor of IOU values.
    """
    expected_size = 2 if anchors else 4
    if boxes1.shape[-1] != expected_size or boxes2.shape[-1] != expected_size:
        raise ShapeError(
            'The number of values representing the bounding box should be {}.'
            .format(expected_size))
    shape1 = tf.shape(boxes1)
    shape2 = tf.shape(boxes2)
    # ensure shape can broadcast
    shape = tf.broadcast_dynamic_shape(shape1, shape2)
    with tf.control_dependencies([shape]):
        boxes1 = tf.identity(boxes1)
    if anchors:
        w1, h1 = tf.unstack(boxes1, axis=-1)
        w2, h2 = tf.unstack(boxes2, axis=-1)
        y1_max, x1_max = h1 / 2, w1 / 2
        y2_max, x2_max = h2 / 2, w2 / 2
        y1_min, x1_min, y2_min, x2_min = -y1_max, -x1_max, -y2_max, -x2_max
    else:
        y1_min, x1_min, y1_max, x1_max = box_to_corners(boxes1, stack=False)
        y2_min, x2_min, y2_max, x2_max = box_to_corners(boxes2, stack=False)
    # intersect corners
    yi_min = tf.maximum(y1_min, y2_min)
    xi_min = tf.maximum(x1_min, x2_min)
    yi_max = tf.minimum(y1_max, y2_max)
    xi_max = tf.minimum(x1_max, x2_max)
    # areas
    area_intersect = area(yi_min, xi_min, yi_max, xi_max)
    area1 = area(y1_min, x1_min, y1_max, x1_max)
    area2 = area(y2_min, x2_min, y2_max, x2_max)
    return area_intersect / (area1 + area2 - area_intersect)


def np_iou(a, b):
    """
    A re-implementation of iou() for numpy...

    a: a box, (N, 4) numpy array of float: x, y, w, h
    b: corner vertices,  (K, 4) numpy array of float.
    returns: a (N, K) ndarray of IOU between boxes and query_boxes.

    reference: https://github.com/rbgirshick/py-faster-rcnn.
    """
    ax, ay, aw, ah = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    bx, by, bw, bh = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    iw = (ax >= bx) * ((bx + bw / 2) - (ax - aw / 2)) + \
        (ax < bx) * ((ax + aw / 2) - (bx - bw / 2))
    ih = (ax >= bx) * ((by + bh / 2) - (ay - ah / 2)) + \
        (ax < bx) * ((ay + ah / 2) - (by - bh / 2))
    iw = np.maximum(iw, 0)
    ih = np.maximum(ih, 0)

    area = bh * bw
    ua = np.expand_dims(ah * aw, axis=1) + area - iw * ih
    ua = np.maximum(ua, np.finfo(float).eps)

    intersection = iw * ih
    return (intersection / ua, iw, ih, intersection, ua)


def np_average_precision(recall, precision):
    """
    Compute the average precision, given the recall and precision curves.

    recall: the recall curve (list).
    precision: the precision curve (list).
    returns: the average precision.

    reference: https://github.com/rbgirshick/py-faster-rcnn.
    """
    # correct AP calculation
    # first append sentinel values at the end
    mrec = np.concatenate(([0.], recall, [1.]))
    mpre = np.concatenate(([0.], precision, [0.]))

    # compute the precision envelope
    for i in range(mpre.size - 1, 0, -1):
        mpre[i - 1] = np.maximum(mpre[i - 1], mpre[i])

    # to calculate area under PR curve, look for points
    # where X axis (recall) changes value
    i = np.where(mrec[1:] != mrec[:-1])[0]

    # and sum (\Delta recall) * prec
    return np.sum((mrec[i + 1] - mrec[i]) * mpre[i + 1])
