import numpy as np
import numpy.linalg as LA
import numpy.random as RD
import torch
import torch.nn.functional as F
import threading, queue
#import timeit
import time
import numpy as np
import pickle
import os
from scipy.special import softmax
from sklearn.decomposition import PCA
import sklearn
import math
import copy
from PIL import Image


#from concurrent.futures import ProcessPoolExecutor
#import multiprocessing as MP

from SCAn import *
import pytorch_ssim

from torch.autograd import Variable
from decimal import Decimal
import utils


RELEASE = False

CONSIDER_LAYER_TYPE = ['Conv2d', 'Linear']
if RELEASE:
    BATCH_SIZE = 128*2
    #BATCH_SIZE = 96
    SVM_FOLDER = '/svm_models'
    CLASSIFIER_MODELPATH = '/heatmap_model.pt'
else:
    BATCH_SIZE = (128*3//4)
    SVM_FOLDER = 'svm_models'
    CLASSIFIER_MODELPATH = 'heatmap_model.pt'
NUM_WORKERS = BATCH_SIZE
EPS = 1e-3
KEEP_DIM = 64
MAX_TRIGGER_SIZE = 600
MULTI_START = 3
SELECT_LAYER = 3
PAIR_CANDI_NUM = 2
HEATMAP_NEURON_NUM = 3
HEATMAP_PER_NEURON = 5


class SingleNeuronAnalyzer:
    def __init__(self, n_idx, init_x, init_y, pipe, n_classes):
        self.n_idx = n_idx
        self.init_x = init_x
        self.init_y = init_y
        #self.init_y = softmax(self.init_y)
        self.pipe = pipe
        self.n_classes = n_classes

        self.x_list = list()
        self.y_list = list()

        self.turn_x = list()
        self.turn_y = list()


    def _f(self, k, x):
        self.x_list.append(x)
        self.pipe.send((k, self.n_idx, x))
        y = self.pipe.recv()
        #y = softmax(y)
        self.y_list.append(y)
        return y

    def _3p_inline(self, x, y):
        lx = x[1]-x[0]
        rx = x[2]-x[1]
        m = len(y[0])
        for j in range(m):
            ly = y[1][j]-y[0][j]
            ry = y[2][j]-y[1][j]
            ''' na = LA.norm([lx,ly]) nb = LA.norm([rx,ry])
            if (na < EPS) or (nb < EPS):
                continue
            #cosa = (lx*rx+ly*ry)/na/nb
            #print([lx, ly, rx, ry,cosa])
            #if abs(cosa) < 1-EPS:
            #    return False
            #sina = (lx*ry-rx*ly)/na/nb
            #if abs(sina) > EPS:
            #    return False
            #'''
            if abs(lx/(lx+rx)*(ly+ry)-ly) > min(EPS*abs(y[0][j]), 1):
                return False
        return True


    def find_lowerest_turn(self, l_x, l_y, r_x, r_y):
        if r_x < l_x+EPS:
            return l_x, l_y

        lx, ly = l_x, l_y
        rx, ry = r_x, r_y
        while lx+EPS < rx:
            dd = rx-lx;
            p1_x = lx+dd*1.0/3.0
            p1_y = self._f(p1_x)
            p2_x = lx+dd*2.0/3.0
            p2_ = self._f(p2_x)


            if self._3p_inline([lx,p1_x,p2_x],[ly,p1_y,p2_y]):
                lx, ly = p2_x, p2_y
            elif self._3p_inline([p1_x,p2_x,rx],[p1_y,p2_y,ry]):
                rx, ry = p1_x, p1_y
            else:
                rx, ry = p2_x, p2_y
        return rx, ry


    def find_bound(self, init_x, init_y, init_delta):
        scale = 1.1
        delta = init_delta
        ix, iy = init_x, init_y
        ok = False

        while not ok:
            lx, ly = ix, iy
            mx, my = lx+delta, self._f(lx+delta)
            while True:
                delta *= scale
                rx, ry = mx+delta, self._f(mx+delta)
                if (abs(mx) > 1e8):
                    break
                if not self._3p_inline([lx,mx,rx],[ly,my,ry]):
                    lx, ly = mx, my
                    mx, my = rx, ry
                else:
                    break

            zx, zy = rx, ry
            ck_list_x = [mx,rx]
            ck_list_y = [my,ry]
            if (abs(mx) > 1e8):
                break
            for i in range(5):
                delta *= scale
                zx, zy = zx+delta, self._f(zx+delta)
                ck_list_x.append(zx)
                ck_list_y.append(zy)
            ok = True
            for i in range(1,6):
                if not self._3p_inline(ck_list_x[i-1:i+1+1], ck_list_y[i-1:i+1+1]):
                    ok = False
                    ix, iy = ck_list_x[i], ck_list_y[i]
                    delta = init_delta
                    break

        return ck_list_x[0], ck_list_y[0]


    def _deal_interval(self, lx, ly, rx, ry):
        if (rx-lx < 0.1):
            return

        mx = (lx+rx)/2.0
        my = self._f(mx)
        if self._3p_inline([lx,mx,rx],[ly,my,ry]):
            return
        self._deal_interval(lx,ly,mx,my)
        self._deal_interval(mx,my,rx,ry)


    def search_trunpoints(self):
        #self._deal_interval(self.l_x, self.l_y, self.r_x, self.r_y)
        #return

        for i in range(5):
            x = RD.random()*(self.r_x-self.l_x)+self.l_x
            y = self._f(x)

        n = len(self.x_list)
        idxs = list(range(n))
        idxs = sorted(idxs, key=lambda z: self.x_list[z])
        int_list = list()
        for i in range(1,n-1):
            a, b, c = idxs[i-1], idxs[i], idxs[i+1]
            xx = [self.x_list[a], self.x_list[b], self.x_list[c]]
            yy = [self.y_list[a], self.y_list[b], self.y_list[c]]
            if self._3p_inline(xx,yy):
                continue
            int_list.append((xx[0],xx[1],yy[0],yy[1]))
            int_list.append((xx[1],xx[2],yy[1],yy[2]))


        while len(self.x_list) < 1000:
            new_int = list()
            for x1,x2,y1,y2 in int_list:
                if (x2-x1 < 0.1):
                    continue
                mx = (x1+x2)/2.0
                my = self._f(mx)
                if self._3p_inline([x1,mx,x2],[y1,my,y2]):
                    continue
                new_int.append((x1,mx,y1,my))
                new_int.append((mx,x2,my,y2))
            if len(new_int) == 0:
                break
            int_list = new_int


    def find_peak(self):
        n = len(self.x_list)
        m = len(self.y_list[0])

        init_y = softmax(self.init_y)
        peak = np.zeros_like(self.y_list[0])
        for z in self.y_list:
            sz = softmax(z)
            for j in range(m):
                peak[j] = max(peak[j], sz[j])

        for j in range(m):
            peak[j] = (peak[j]-init_y[j])

        init_lb = np.argmax(init_y)
        peak[init_lb] = 0

        self.peak = peak


    def bf_check(self):
        init_pred = np.argmax(self.init_y, axis=1)
        n = len(self.init_x)
        m = self.n_classes

        pred = list()
        max_v = np.max(self.init_x)
        for i in range(n):
            logits = self._f(i,max_v*2)
            pred.append(np.argmax(logits))

        mat = np.zeros([m,m])
        for i in range(n):
            mat[init_pred[i]][pred[i]] += 1
        for i in range(m):
            mat[i] /= np.sum(mat[i])

        self.peak = np.zeros(m)
        for i in range(m):
            for j in range(m):
                if i == j:
                    continue
                self.peak[j] = max(self.peak[j], mat[i][j])


    def run(self):
        self.bf_check()
        return self.peak


        self.l_x, self.l_y = self.find_bound(self.init_x, self.init_y, -10)
        self.r_x, self.r_y = self.find_bound(self.init_x, self.init_y, 10)

        print([self.l_x, self.r_x])
        #print([self.l_y, self.r_y])

        self.search_trunpoints()
        self.find_peak()
        return self.peak


class PredictingThread(threading.Thread):
    def __init__(self, p_func):
        threading.Thread.__init__(self)
        self.p_func = p_func
        self.pipes = list()
        self.lets_run = False

    def get_pipe(self):
        me, you = MP.Pipe()
        self.pipes.append(me)
        return you

    def start(self):
        self.lets_run = True
        super().start()

    def join(self):
        self.lets_run = False
        super().join()

    def run(self):
        print('start listening')
        x_list = list()
        r_pipes = list()
        while self.lets_run:
            ready = MP.connection.wait(self.pipes, timeout=0.001)
            if not ready:
                continue

            for pipe in ready:
                while pipe.poll():
                    try:
                        x_list.append(pipe.recv())
                        r_pipes.append(pipe)
                    except EOFError as e:
                        pipe.close()

            if len(x_list) == 0:
                continue

            #print(len(x_list))

            while True:
                x_try = x_list[:BATCH_SIZE]
                p_try = r_pipes[:BATCH_SIZE]

                ys = self.p_func(x_try)
                for pipe, y in zip(p_try, ys):
                    pipe.send(y)

                x_list = x_list[BATCH_SIZE:]
                r_pipes = r_pipes[BATCH_SIZE:]
                if len(x_list) < BATCH_SIZE:
                    break

        print('stop listening')



def get_model_name(model):
    mds = list(model.modules())
    model_name = type(mds[0]).__name__
    model_name = model_name.lower()
    return model_name

def _expand_childs(childs):
    rst = list()
    for i in range(len(childs)):
        _name = type(childs[i]).__name__
        if _name == 'Sequential':
            _chs = list(childs[i].children())
            for ch in _chs:
                rst.append(ch)
        else:
            rst.append(childs[i])
    return rst

def _make_childs_inception3(model):
    childs = list(model.children())
    if not isinstance(childs[3], torch.nn.MaxPool2d):
        childs.insert(3, torch.nn.MaxPool2d(kernel_size=3, stride=2))
    if not isinstance(childs[6], torch.nn.MaxPool2d):
        childs.insert(6, torch.nn.MaxPool2d(kernel_size=3, stride=2))
    childs = _expand_childs(childs)
    childs.insert(-1, torch.nn.Flatten())
    return childs

def _make_childs_densenet(model):
    _childs = list(model.children())
    childs = list(_childs[0].children())
    childs.append(torch.nn.ReLU(inplace=True))
    childs.append(torch.nn.AdaptiveAvgPool2d((1,1)))
    childs.append(torch.nn.Flatten(1))
    childs.append(_childs[1])
    return childs

def _make_childs_squeezenet(model):
    childs = list(model.children())
    childs = _expand_childs(childs)
    childs.append(torch.nn.Flatten(1))
    return childs

def _make_childs_general(model):
    _childs = list(model.children())
    childs = _expand_childs(_childs[:-1])
    if type(childs[-1]).__name__ == 'Dropout' and type(childs[-2]).__name__ == 'AdaptiveAvgPool2d':
        pass
    elif type(childs[-1]).__name__ == 'AdaptiveAvgPool2d':
        pass
    else:
        childs.append(torch.nn.AdaptiveAvgPool2d((1,1)))

    childs.append(torch.nn.Flatten(1))
    _name = type(_childs[-1]).__name__
    if _name == 'Sequential':
        _chs = list(_childs[-1].children())
        for ch in _chs:
            childs.append(ch)
    else:
        childs.append(_childs[-1])
    return childs

def make_childs(model, model_name=None):
    if model_name is None:
        model_name = get_model_name(model)

    if model_name == 'inception3':
        childs = _make_childs_inception3(model)
    elif model_name == 'densenet':
        childs = _make_childs_densenet(model)
    elif model_name == 'squeezenet':
        childs = _make_childs_squeezenet(model)
    else:
        childs = _make_childs_general(model)
    return childs

def module_flatten(module_tree):
    module_list = list()
    if type(module_tree) is not list:
        childs = list(module_tree.children())
    else:
        childs = module_tree
    if len(childs) == 0:
        module_list.append(module_tree)
    else:
        for md in childs:
            _list = module_flatten(md)
            module_list.extend(_list)
    return module_list

def build_model_from_childs(childs, st_child=0, ed_child=None):
    len_childs = len(childs)
    if ed_child is None:
        ed_child = len_childs
    st_child = max(st_child, 0)
    ed_child = min(ed_child, len_childs)

    _list = childs[st_child:ed_child]

    model = torch.nn.Sequential(*childs[st_child:ed_child])
    return model



class NeuronAnalyzer:
    def __init__(self, model, n_classes):
        self.model = model.cuda()
        self.model.eval()
        self.model_name = get_model_name(self.model)
        print(self.model_name)

        ''' #test lrp
        import skimage.io
        img = skimage.io.imread('class_0_example_3.png')
        h,w,c = img.shape
        dh = (h-224)//2
        dw = (w-224)//2
        raw_img = img[dh:dh+224,dw:dw+224,:]
        img = np.transpose(raw_img,(2,0,1))

        #img = (img-np.min(img))/(np.max(img)-np.min(img))
        img = np.expand_dims(img,axis=0)
        img = utils.regularize_numpy_images(img)
        aimg = np.concatenate([img])

        childs = make_childs(self.model)

       
        self.lrp = LRP(self.model)
        hook = None
        #hook = (childs[8],12,69) #for densenet161 0683.model
        #hook = (childs[9],139,17) #for resnet18 5->4 0766.model poisoned
        #hook = (childs[9],111,23) #for resnet18 15->13 0713.model benign
        #hook = (childs[37],496,214) #for 5->6 0140.model vgg16bn poisoned
        #hook = (childs[34],299,165) #for 6->4 0165.model vgg16bn benign
        #hook = (chidls[34],299,165) #for 6->4 0167.model mobilenetv2 benign
        heatmap = self.lrp.interpret(aimg, hook) 

        #from LRP.lrp import LRP as oLRP
        #lrp = oLRP(self.model, 'z_plus', input_lowest=0)
        #in_tensor = torch.FloatTensor(aimg)
        #heatmaps = lrp.relprop(in_tensor.cuda())
        #heatmaps = heatmaps.detach().cpu().numpy()
        #heatmaps = np.sum(heatmaps, axis=1)

        utils.demo_heatmap(heatmap[0],'haha.png')
        exit(0)
        #'''



        self.hook_activate = False


        self.n_classes = n_classes

        self.results = list()

        #self.manager = MP.Manager()
        #self.output_queue = queue.Queue()

        self.batch_size = BATCH_SIZE

        self.hook_layer, self.hook_channels, self.hook_values = -1,None,None


    def set_out_folder(self, scratch_path):
        self.scratch_folder = scratch_path


    def get_record_hook(self, k_layer):
        def hook(model, input, output):
            if type(input) is tuple:
                input = input[0]
            if type(output) is tuple:
                output = output[0]
            self.md_child[k_layer] = self.current_child
            if not self.record_conv:
                return
            self.inputs[k_layer].append(input.cpu().detach().numpy())
            self.outputs[k_layer].append(output.cpu().detach().numpy())
        return hook


    def start_record_conv_layers(self):
        self.conv_inputs=list()
        self.conv_outputs=list()
        for i in range(len(self.convs)):
            self.conv_inputs.append(list())
            self.conv_outputs.append(list())
        self.record_conv=True


    def stop_record_conv_layers(self):
        self.record_conv=False
        for i in range(len(self.convs)):
            if len(self.conv_inputs[i]) == 0:
                continue
            self.conv_inputs[i] = np.concatenate(self.conv_inputs[i])
            self.conv_outputs[i] = np.concatenate(self.conv_outputs[i])


    def save_images(self, show_ims):
        if len(show_ims.shape) == 4:
            show_ims = show_ims[0]
        from misc_functions import save_image
        for i,show_im in enumerate(show_ims):
            immax = np.max(show_im)
            immin = np.min(show_im)
            if immax-immin < 1e-9:
                show_im -= immin
            else:
                show_im = (show_im - immin)/(immax-immin)
            save_image(show_im,'%d_haha.jpg'%i)


    def get_modify_hook(self, k_layer):
        def hook(model, input, output):
            if type(output) is tuple:
                output = output[0]
            if type(input) is tuple:
                input = input[0]

            if self.record_conv:
                self.conv_outputs[k_layer].append(output.cpu().detach().numpy())
                self.conv_inputs[k_layer].append(input.cpu().detach().numpy())

            if type(self.hook_layer) is int and  k_layer != self.hook_layer:
                return
            if type(self.hook_layer) is list and  k_layer not in self.hook_layer:
                return

            ori = output

            if type(self.hook_channels) is int:
                if self.hook_channels < 0:
                    ori[:,:,:,:] = self.hook_values
                else:
                    ori[:,self.hook_channels] = self.hook_values
            elif type(self.hook_channels) is list:
                for chnn,v in zip(self.hook_channels, self.hook_values):
                    if type(v) is np.ndarray:
                        ori[:,chnn] = torch.from_numpy(v).to(ori)
                    elif type(v) is int and v < 0:
                        maxv = F.adaptive_max_pool3d(ori,(1,1,1))
                        maxv = torch.squeeze(maxv,1)
                        bef = ori[:,chnn,:,:]
                        bef_max = F.adaptive_max_pool2d(bef,(1,1))
                        shape = bef.shape
                        h,w = shape[-2], shape[-1]
                        dh = h//4
                        dw = w//4
                        rolled = bef*1.0
                        rolled[:,dh:,dw:] = bef[:,:h-dh,:w-dw]
                        rolled = F.relu(rolled)

                        mixed = torch.max(bef,rolled)
                        #'''
                        for z in range(len(mixed)):
                            if torch.max(mixed[z]) < 1e-7:
                                continue
                            _b = bef[z]*self.tensor_mean[self.hook_layer]/bef_max[z]
                            _r = rolled[z] * self.tensor_mean[self.hook_layer]/bef_max[z]
                            mixed[z,:,:] = torch.max(_b, _r)
                        #'''


                        ori[:,chnn,:,:] = mixed
                    else:
                        ori[:,chnn,:,:] = v

            return ori.cuda()
        return hook


    def get_child_pre_hook(self, k_layer):
        def hook(model, input):
            self.current_child = k_layer
            if type(input) is tuple:
                input = input[0]
            if not self.record_child:
                return
            self.child_inputs[k_layer].append(input.cpu().detach().numpy())
        return hook

    def get_child_hook(self, k_layer):
        def hook(model, input, output):
            if type(output) is tuple:
                output = output[0]
            if not self.record_child:
                return
            self.child_outputs[k_layer].append(output.cpu().detach().numpy())
        return hook


    def _init_general_md(self):
        mds = list(self.model.modules())

        self.convs = list()
        self.relus = list()
        self.inputs = list()
        self.outputs = list()
        self.conv_weights = list()
        self.md_child = list()
        for md in mds:
            na = type(md).__name__
            if na == 'Conv2d':
                self.convs.append(md)
                self.inputs.append([])
                self.outputs.append([])
                self.conv_weights.append(md.weight.cpu().detach().numpy())
                self.md_child.append(0)
            elif na == 'ReLU':
                self.relus.append(md)
            
        print(self.model_name, len(self.convs), 'convs')


 
    def _init_hooks(self):
        self.record_conv = False
        self.hook_handles = list()
        for k,md in enumerate(self.convs):
            self.hook_handles.append(md.register_forward_hook(self.get_record_hook(k)))

        self.current_child = -1

        self.child_inputs = list()
        self.child_outputs = list()
        self.record_child = False
        for k,c in enumerate(self.childs):
            self.hook_handles.append(c.register_forward_pre_hook(self.get_child_pre_hook(k)))
            self.hook_handles.append(c.register_forward_hook(self.get_child_hook(k)))
            self.child_inputs.append([])
            self.child_outputs.append([])


    def add_to_output_queue(self, out_fn, x_list, y_list):
        n = len(x_list)
        m = len(y_list[0])
        idxs = list(range(n))
        sorted_idxs = sorted(idxs, key=lambda z: x_list[z])

        tt_list = list()
        for idx in sorted_idxs:
            out_tmp = list()
            out_tmp.append(x_list[idx])
            for y in y_list[idx]:
                out_tmp.append(y)
            tt_list.append(out_tmp)

        self.output_queue.put((out_fn, np.asarray(tt_list)))


    def output_func(self):
        while self.output_run:
            try:
                fn, data = self.output_queue.get(timeout=0.1)
                with open(fn,'wb') as f:
                    np.save(f,data)
            except queue.Empty:
                continue


    def start_output_thread(self):
        self.output_run = True
        self._t_output = threading.Thread(target=self.output_func, name='output_thread')
        self._t_output.start()


    def stop_output_thread(self):
        self.output_run = False
        self._t_output.join()


    def recall_fn(self, future):
        k, idx, data = future.result()
        #idx, data, x_list, y_list = future.result()
        self.results.append((k, idx,data))
        #out_fn = 'logs/log_neuron_'+str(idx)+'.npy'
        #self.add_to_output_queue(out_fn, x_list, y_list)


    def adjust_batchsize(self, batch_size):
        if self.arch_name in ['googlenet','inceptionv3','mobilenetv2']:
            batch_size //= 4
            batch_size *= 3
        elif self.arch_name in ['vgg11bn','vgg13bn','resnet50']:
            batch_size //= 8
            batch_size *= 3
        elif self.arch_name in ['vgg16bn','vgg19bn','densenet121','resnet101','wideresnet50']:
            batch_size //= 4
        elif self.arch_name in ['densenet169','densenet201','resnet152','wideresnet101', 'densenet161']:
            batch_size //= 16
            batch_size *= 3

        print('adjust batch_size to', batch_size)
        return batch_size


    def get_init_values(self, all_X, all_Y):

        self.childs = make_childs(self.model)

        #for z,ch in enumerate(self.childs):
        #    print(z,ch)
        #exit(0)

        self._init_general_md()
        self.arch_name = self.get_arch_name()
        print('model architecture:', self.arch_name)
        self.batch_size = self.adjust_batchsize(BATCH_SIZE)

        acc_ct = 0
        tot_n = 0
        self.images = list()
        self.raw_images_all = list()
        self.preds_init = list()
        self.logits_init = list()
        self.ori_lbs = list()

        n_imgs = len(all_Y)
        bs = self.batch_size
        for step in range(0,n_imgs, bs):
            raw_imgs, lbs = all_X[step:step+bs], all_Y[step:step+bs]

            #normalize to [0,1]
            np_imgs = utils.regularize_numpy_images(raw_imgs)

            tot_n += len(lbs)
            lbs = lbs.squeeze(axis=-1)
            lbs = lbs.astype(np.int32)

            imgs_tensor = torch.from_numpy(np_imgs)
            y_tensor = self.model(imgs_tensor.cuda())
            logits = y_tensor.cpu().detach().numpy()
            pred = np.argmax(logits,axis=1)

            correct_idx = (lbs==pred)
            #correct_idx = (pred==pred)
            acc_ct += sum(correct_idx)

            self.preds_init.append(pred[correct_idx])
            self.logits_init.append(logits[correct_idx])
            self.images.append(np_imgs[correct_idx])
            self.raw_images_all.append(raw_imgs[correct_idx])
            self.ori_lbs.append(lbs[correct_idx])

        del imgs_tensor, y_tensor
        torch.cuda.empty_cache()
        print(acc_ct/tot_n)

        self.preds_init = np.concatenate(self.preds_init)
        self.logits_init = np.concatenate(self.logits_init)
        self.images = np.concatenate(self.images)
        self.raw_images_all = np.concatenate(self.raw_images_all)
        self.ori_lbs = np.concatenate(self.ori_lbs)

        self.preds_all = self.preds_init
        self.logits_all = self.logits_init
        self.images_all = self.images

        print('before _init_hooks')
        self._init_hooks()
        last_md = self.childs[-1]
        if type(last_md).__name__ == 'Linear':
            self.n_classes = last_md.out_features
        else:
            self.n_classes = self.convs[-1].out_channels
        print('n_classes',self.n_classes)

        cat_cnt = [0]*self.n_classes
        for lb in range(self.n_classes):
            cat_cnt[lb] = np.sum(self.ori_lbs == lb)
        for lb in range(self.n_classes):
            cat_cnt[lb] = max(min(math.ceil(0.1*cat_cnt[lb])*2+1, 10),5)

        
        self.record_conv = True
        self.record_child = False
        self._run_once_epoch(self.images)
        print(self.md_child)

        self._extract_statistical_data()

        #'''
        #trim samples
        trim_idx = self.preds_init<0
        ct = [0]*self.n_classes
        for i in range(len(self.preds_init)):
            lb = self.ori_lbs[i]
            if ct[lb] < cat_cnt[lb]:
                trim_idx[i] = True
                ct[lb] += 1
        print('examples count', ct)
        self.lbs_init = self.ori_lbs[trim_idx]
        self.preds_init = self.preds_init[trim_idx]
        self.logits_init = self.logits_init[trim_idx]
        self.images = self.images[trim_idx]
        self.raw_images_init = self.raw_images_all[trim_idx]
        #'''

        self.record_conv = True
        self.record_child = True
        self._run_once_epoch(self.images)

        for i in range(len(self.childs)):
            if len(self.child_inputs[i]) > 0:
                self.child_inputs[i] = np.concatenate(self.child_inputs[i])
            if len(self.child_outputs[i]) > 0:
                self.child_outputs[i] = np.concatenate(self.child_outputs[i])
        for i in range(len(self.inputs)):
            if len(self.inputs[i]) == 0:
                continue
            self.inputs[i] = np.concatenate(self.inputs[i])
            self.outputs[i] = np.concatenate(self.outputs[i])

        for handle in self.hook_handles:
            handle.remove()
        self.hook_handles = list()

        self.record_conv = False
        self.record_child = False



    def _extract_statistical_data(self):
        lb_idx = list()
        for lb in range(self.n_classes):
            lb_idx.append(self.preds_all==lb)

        self.mean_channel_max = list()
        self.lb_channel_max = list()
        self.lb_channel_mean = list()
        self.channel_max = list()
        self.channel_min = list()
        self.channel_std = list()
        self.channel_mean = list()
        self.channel_lb = list()
        self.tensor_max = list()
        self.tensor_min = list()
        self.tensor_mean = list()
        out_channel_sum = 0
        self.channel_in_max = list()
        self.channel_in_min = list()
        for k in range(len(self.outputs)):
            ttmp = list()
            mtmp = list()
            ztmp = list()
            for ot in self.outputs[k]:
                tmat = np.max(ot,(2,3))
                relu_ot = np.maximum(ot,0)
                mmat = np.mean(relu_ot,(2,3))
                zmat = np.min(ot,(2,3))

                ttmp.append(tmat)
                mtmp.append(mmat)
                ztmp.append(zmat)
            ttmp = np.concatenate(ttmp)
            mtmp = np.concatenate(mtmp)
            ztmp = np.concatenate(ztmp)
            n_chnn = ttmp.shape[1]

            max_lb = list()
            mean_lb = list()
            for lb in range(self.n_classes):
                if np.sum(lb_idx[lb]) == 0:
                    max_lb.append(np.zeros((1,ttmp.shape[1])))
                    mean_lb.append(np.zeros((1,mtmp.shape[1])))
                else:
                    z = np.mean(ttmp[lb_idx[lb]],0,keepdims=True)
                    max_lb.append(z)
                    z = np.mean(mtmp[lb_idx[lb]],0,keepdims=True)
                    mean_lb.append(z)
            max_lb = np.concatenate(max_lb)
            mean_lb = np.concatenate(mean_lb)
            self.lb_channel_max.append(max_lb)
            self.lb_channel_mean.append(mean_lb)

            lb_list = list()
            for i in range(n_chnn):
                lb_list.append(np.argmax(self.lb_channel_max[k][:,i]))
            self.channel_lb.append(lb_list)

            self.mean_channel_max.append(np.mean(ttmp,0))

            self.channel_max.append(np.max(ttmp,0))
            self.channel_min.append(np.min(ztmp,0))
            self.channel_mean.append(np.mean(mtmp,0))

            self.tensor_max.append(np.max(ttmp))
            self.tensor_min.append(np.min(ztmp))
            self.tensor_mean.append(np.mean(np.max(ttmp,1)))
            out_channel_sum += n_chnn

            itmp = list()
            ntmp = list()
            for it in self.inputs[k]:
                imat = np.max(it,(2,3))
                itmp.append(imat)
                nmat = np.min(it,(2,3))
                ntmp.append(nmat)
            itmp = np.concatenate(itmp)
            ntmp = np.concatenate(ntmp)
            self.channel_in_max.append(np.max(itmp,0))
            self.channel_in_min.append(np.max(ntmp,0))


            if k==22:
                record_data = np.concatenate(self.outputs[k])
                self.dummy_data = record_data
                '''
                fn = 'id-00000369_l22_out.npy'
                with open(fn,'wb') as f:
                    np.save(f,record_data)
                print('write to',fn)
                exit(0)
                '''


            self.inputs[k] = []
            self.outputs[k] = []
        print('total channels '+str(out_channel_sum))



    def register_representation_record_hook(self):
        md = self.childs[-1]

        def hook(model, input, output):
            if not self.record_reprs:
                return
            if type(input) is tuple:
                input = input[0]
            self.reprs.append(input.cpu().detach().numpy())

        md.register_forward_hook(hook)


    def _get_partial_model(self, st_child, ed_child=None):
        len_childs = len(self.childs)
        if ed_child is None:
            ed_child = len_childs
        st_child = max(st_child, 0)
        ed_child = min(ed_child, len_childs)

        _list = self.childs[st_child:ed_child]

        model = torch.nn.Sequential(*self.childs[st_child:ed_child])
        return model

    def _run_once_epoch_with_model(self, inputs, model):
        outputs = list()
        tn = len(inputs)
        for st in range(0,tn, self.batch_size):
            imgs = inputs[st:min(st+self.batch_size,tn)]
            imgs_tensor = torch.FloatTensor(imgs)
            y_tensor = model(imgs_tensor.cuda())
            y = y_tensor.cpu().detach().numpy()
            outputs.append(y)
        del imgs_tensor, y_tensor
        outputs = np.concatenate(outputs)
        return outputs

    def _run_once_epoch(self, inputs, st_child=0, ed_child=None):
        if type(ed_child) is int and ed_child < 0:
            return inputs

        model = self._get_partial_model(st_child, ed_child)
        outputs = self._run_once_epoch_with_model(inputs, model)
        del model
        torch.cuda.empty_cache()

        if ed_child is None:
            preds = np.argmax(outputs, axis=1)
            return (outputs, preds)
        return outputs


    def score_to_prob(self, score):
        return score
        print(score)
        a = 1.0
        b = -5
        z = np.log(score)
        w = np.tanh(a*z+b)
        w = w/2.0+0.5
        return w


    def test_layer(self, k_layer, test_conv=False, test_maxv=None):
        self.clear_hook_trigger()
        self.batch_size = self.adjust_batchsize(BATCH_SIZE)
        if test_conv:
            st_child = self.md_child[k_layer]
            inner_outputs = self.child_inputs[st_child]
            inner_shape = self.outputs[k_layer].shape
            
            _coef = min(1, 0.5*(1-k_layer/len(self.convs)))
            self.batch_size = int(self.batch_size/_coef)

        else:
            st_child = k_layer+1
            inner_outputs = self.child_inputs[st_child]
            test_maxv = np.max(inner_outputs)*2.0
            inner_shape = inner_outputs.shape

            _coef = min(1, (1-0.8*k_layer/len(self.childs)))
            self.batch_size = int(self.batch_size/_coef)

        print('test_layer change batch_size to',self.batch_size)
        ori_labels = self.preds_init

        base_v = None
        '''
        fn = 'id-00000369_l22_out.npy'
        load_data = np.load(fn)
        base_v = load_data[3,6,:,:]
        base_v /= np.max(np.abs(base_v))
        _img = Image.fromarray(base_v)
        _img = _img.resize((inner_shape[-2],inner_shape[-1]), resample=Image.BILINEAR)
        base_v = np.array(_img)
        #'''

        tail_model = self._get_partial_model(st_child=st_child)

        neuron_dict = dict()
        chnn_rst = dict()
        n_inputs, n_chnn = inner_shape[0], inner_shape[1]
        lb_mat = np.zeros((self.n_classes,self.n_classes), dtype=np.int32)
        for i in range(n_chnn):
            #print('-----------------%d/%d'%(i,n_chnn))
            logits_list = list()

            if type(test_maxv) is np.ndarray:
                _testv = test_maxv[i]
            else:
                _testv = test_maxv

            low_v, s_lb, t_lb = self.search_lower_bound(k_layer, i, _testv, inner_outputs, ori_labels, tail_model, logits_list, base_v, test_conv) 

            neuron_dict[(k_layer, i, low_v)] = (s_lb, t_lb)

            if s_lb >= 0 and t_lb >= 0:
                lb_mat[s_lb,t_lb] += 1

            #print(low_v, s_lb, t_lb)
            logits_mat = np.asarray(logits_list)

            _inputs = inner_outputs.copy()
            if test_conv:
                self.clear_hook_trigger()
                self.set_hook_trigger(k_layer,i,0)
            else:
                _inputs[:,i,:,:] = 0
            zero_logits = self._run_once_epoch_with_model(_inputs, tail_model)
            if test_conv:
                self.clear_hook_trigger()

            flb_ct = np.zeros(self.n_classes)
            diff_rst = list()
            for x in range(n_inputs):
                logits = logits_mat[:,x,:]
                diff = np.amax(logits,axis=0)-zero_logits[x]
                order = np.argsort(diff)
                f_lb, s_lb = order[-1], order[-2]
                diff_rst.append((f_lb, diff[f_lb]-diff[s_lb]))
                flb_ct[f_lb] += 1

            tgt_lb = np.argmax(flb_ct)

            values = list()
            for x in range(n_inputs):
                if ori_labels[x] == tgt_lb:
                    continue
                t_lb, d_v = diff_rst[x]
                if t_lb != tgt_lb:
                    continue
                values.append(d_v)

            if len(values) > 0:
                chnn_rst[(k_layer,i)] = (tgt_lb, flb_ct[f_lb], min(values), values)
                #print((k_layer,i), (t_lb, tgt_lb), flb_ct[f_lb], min(values))
            else:
                chnn_rst[(k_layer,i)] = (tgt_lb, 0, 0, values)
                #print((k_layer,i), tgt_lb, 0)

        del tail_model

        print(lb_mat)
        return chnn_rst, lb_mat, neuron_dict


    def select_candi(self, candi_dict, top_k=10, thr=0.9):
        n_imgs = len(self.images)
        keys = list()
        for key in candi_dict:
            value = candi_dict[key]
            miss_ct = value[1]
            if miss_ct >= n_imgs*thr:
                keys.append(key)
        sorted_key = sorted(keys, key=lambda x:-candi_dict[x][2])

        selected_candi = list()
        lb_ct = np.zeros(self.n_classes)
        for key in sorted_key:
            k,i = key
            t_lb, diff_v = candi_dict[key][0], candi_dict[key][2]
            #print((k,i),(t_lb,diff_v))
            if (k,i,t_lb) in selected_candi:
                continue
            if lb_ct[t_lb] == 0:
                lb_ct[t_lb] += 1
                selected_candi.append((k,i,t_lb,diff_v))

            if len(selected_candi) > top_k:
                break

        return selected_candi


    def _check_preds_backdoor(self, ori_lbs, logits, t_lb=None):
        n = logits.shape[0]
        m = self.n_classes
        mat = np.zeros((m,m))
        ct = np.zeros(m)
        for i in range(n):
            lb = ori_lbs[i]
            ct[lb] += 1
            tg = np.argmax(logits[i])
            mat[lb,tg] += 1
            #continue
            #prob = softmax(logits[i])
            #mat[lb] += prob
        for lb in range(m):
            mat[lb] /= ct[lb]

        if t_lb is None:
            tgt = np.zeros(m)
            kep = np.zeros(m)
            for j in range(m):
                for i in range(m):
                    if i == j:
                        kep[j] = mat[i,j]
                        continue
                    if mat[i,j] > tgt[j]:
                        tgt[j] = mat[i,j]

            tgt *= kep
            arg = np.argsort(tgt)
            fi_lb, se_lb = arg[-1], arg[-2]
            return (fi_lb,tgt[fi_lb]), (se_lb,tgt[se_lb]), tgt, mat
        else:
            tgt = mat[:,t_lb]
            kep = tgt[t_lb]
            tgt[t_lb] = -1
            tgt *= kep
            arg = np.argsort(tgt)
            fi_lb, se_lb = arg[-1], arg[-2]
            return (fi_lb,tgt[fi_lb]), (se_lb,tgt[se_lb]), tgt, mat



    def search_lower_bound(self, k_layer, chnn_i, r_limit, o_inputs, o_labels, model, record_logits_list, base_v=None, test_conv=False):
        if base_v is None:
            base_v=1
        tgt_lb = None

        #print('search_lower_bound',(k_layer,chnn_i), r_limit)

        def _get_acc(testv):
            self.clear_hook_trigger()
            hahav = base_v*testv
            if test_conv:
                self.clear_hook_trigger()
                self.set_hook_trigger(k_layer,chnn_i,hahav)
                _inputs = o_inputs
            else:
                _inputs = o_inputs.copy()
                _inputs[:,chnn_i,:,:] = hahav

            logits = self._run_once_epoch_with_model(_inputs, model)

            if test_conv:
                self.clear_hook_trigger()
            record_logits_list.append(logits)
            f_pair, s_pair, _, _ = self._check_preds_backdoor(o_labels, logits, tgt_lb)
            return f_pair[1], f_pair[0], s_pair[1], s_pair[0]

        lv = 0
        rv = r_limit
        thr = 0.95
        _eps = max(EPS,min(1.0,r_limit/1000.0))

        self.record_conv = False
        self.record_reprs = False


        acc, tgt_lb, acc2, lb2 = _get_acc(rv)
        #print(acc,tgt_lb,acc2,lb2, r_limit)
        if acc < thr or acc-acc2 < 0.5:
            #print('lower_bound:','fail by acc',acc)
            return -1,-1,-1

        while lv+_eps < rv:
            mv = (lv+rv)/2.0
            acc, s_lb, acc2, lb2 = _get_acc(mv)
            #print(mv,acc,acc2,s_lb,lb2)

            if acc < thr:
                lv=mv
            else:
                rv=mv

        #print('lower_bound:', (acc, acc2), (rv,r_limit), '%d->%d'%(s_lb,tgt_lb))

        return rv, s_lb, tgt_lb


    def regular_features(self, fe):
        shape = fe.shape
        if len(shape) > 2:
            fe = np.average(fe,axis=(2,3))
        return fe

    def set_hook_trigger(self,layer,channel,testv):
        if layer >= 0 and layer != self.hook_layer:
            self.hook_channels = list()
            self.hook_values = list()
        self.hook_layer = layer
        if layer >= 0:
            self.hook_channels.append(channel)
            self.hook_values.append(testv)
        else:
            self.hook_channels = None
            self.hook_values = None

    def clear_hook_trigger(self):
        self.set_hook_trigger(-1,-1,-1)



    def zero_test(self,k,i):
        self.clear_hook_trigger()
        self.set_hook_trigger(k,i,0)
        logits, preds = self._run_once_epoch(self.images_all)
        self.clear_hook_trigger()

        probs = list()
        for logit in logits:
            probs.append(softmax(logit))
        probs = np.asarray(probs)

        probs_ori = list()
        logits_ori = self.logits_all
        for logit in logits_ori:
            probs_ori.append(softmax(logit))
        probs_ori = np.asarray(probs_ori)
        #diff = probs_ori- probs
        diff = logits_ori - logits
        mean_diff = np.zeros(self.n_classes)
        for lb in range(self.n_classes):
            idx = self.preds_all==lb
            if np.sum(idx) == 0:
                mean_diff[lb] = 0
            else:
                mean_diff[lb] = np.mean(diff[idx,lb])
        if np.max(mean_diff) > 1e-1:
            print(k,i,np.argmax(mean_diff), np.max(mean_diff), self.channel_lb[k][i])
        else:
            print('aiaai')


    def update_relus(self):

        def relu_backward_hook_function(module, grad_in, grad_out):
            forward_output = self.forward_relu_outputs[-1]
            forward_output_sign = (forward_output>0)
            modified_grad_out = forward_output_sign*torch.clamp(grad_in[0], min=0.0)
            del self.forward_relu_outputs[-1]
            return (modified_grad_out,)

        def relu_forward_hook_function(module, ten_in, ten_out):
            self.forward_relu_outputs.append(ten_out)

        for md in self.relus:
            self.relu_hook_handles.append(md.register_backward_hook(relu_backward_hook_function))
            self.relu_hook_handles.append(md.register_forward_hook(relu_forward_hook_function))


    def test_one_channel(self, k, i, try_v, candi_list):
        self.clear_hook_trigger()
        self.set_hook_trigger(k,i,try_v)

        #if k == 19:
        #    print('haha')
        #    self.hook_layer = -1
        st_child = self.md_child[k]
        logits, preds = self._run_once_epoch(self.child_inputs[st_child], st_child)
        self.clear_hook_trigger()

        #pair, mat, att_acc = self._check_preds_backdoor(self.preds_init, preds, 0.5)
        f_pair, s_pair, _, _ = self._check_preds_backdoor(self.lbs_init, logits)

        if f_pair[1]-s_pair[1] > 0.5:
            candi_list.append(((k,i,try_v), (f_pair[1],s_pair[1])))

        return f_pair, s_pair


    def guided_grad_test(self, param, pair):
        k, i, try_v = param[0], param[1], param[2]
        u, v = pair

        s_idx = (self.preds_all==u)

        self.clear_hook_trigger()
        self.set_hook_trigger(k,i,try_v)

        self.forward_relu_outputs = list()
        self.relu_hook_handles = list()
        self.update_relus()

        self.model.zero_grad()

        test_images = self.images_all[s_idx]
        imgs_tensor = torch.from_numpy(test_images).cuda()
        input_variable = Variable(imgs_tensor, requires_grad=True)
        y_tensor = self.model(input_variable)

        logits = y_tensor.data.cpu().numpy()
        print(np.argmax(logits,1))

        one_hot_output = torch.FloatTensor(y_tensor.size()).zero_()
        one_hot_output[:,v] = 1.0

        y_tensor.backward(gradient=one_hot_output.cuda())

        guided_grads = input_variable.grad.data.cpu().numpy()

        for handle in self.relu_hook_handles:
            handle.remove()
        self.relu_hook_handles = list()

        pos_saliency_list = list()
        for grad in guided_grads:
            saliency = np.maximum(0,grad) / grad.max()
            pos_saliency_list.append(saliency)
        pos_saliency_list = np.asarray(pos_saliency_list)


        from misc_functions import save_image

        folder = '../results'
        if not os.path.exists(folder):
            os.amkedirs(foler)
        for i, grad in enumerate(pos_saliency_list):
            print(np.sum(grad))
            grad = grad-grad.min()
            grad /= grad.max()
            path_to_file = os.path.join(folder, 'grad_%d.jpg'%i)
            save_image(grad, path_to_file)
            path_to_file = os.path.join(folder, 'ori_%d.jpg'%i)
            save_image(test_images[i], path_to_file)


        exit(0)


    def _clear_modify_hooks(self):
        for h in self.modify_hook_handles:
            h.remove()
        self.modify_hook_handles.clear()


    def reverse_trigger(self, param, pair, data, test_conv=False):
        print('reverse', param[0:2], pair)

        k, i = param[0], param[1]
        u, v = pair

        images, labels = data

        slb_idx = labels==u
        raw_images = images[slb_idx]

        idx = np.random.permutation(len(raw_images))
        select_n = min(10, len(images)//2)
        select_idx = idx[:select_n]
        reverse_images = raw_images[select_idx]

       
        self._clear_modify_hooks()

        if test_conv:
            _list = self.convs
        else:
            _list = self.childs
        reverser = Reverser(self.model, param, pair, _list)
        best_mask, best_raw_pattern, best_mask_loss = reverser.reverse(reverse_images)

        if best_mask is None:
            return 0, None, None, best_mask_loss, best_mask_loss

        best_mask_nz = np.sum(best_mask>0.2)

        print('----------------------')

        raw_poisoned_images = ((1-best_mask)*raw_images + best_mask*best_raw_pattern)

        #normalize to [0,1]
        np_imgs = raw_poisoned_images-raw_poisoned_images.min((1,2,3), keepdims=True)
        np_imgs = np_imgs/np_imgs.max((1,2,3), keepdims=True)

        logits, preds = self._run_once_epoch(np_imgs)

        hit_idx = (preds==v)
        att_acc = np.sum(hit_idx)/len(preds)
        probs = list()
        for logit in logits:
            probs.append(softmax(logit))
        probs = np.asarray(probs)
        avg_prob = np.mean(probs[:,v])
        print(raw_poisoned_images.shape)
        print('%d->%d'%(u,v),'att_acc:',att_acc, 'avg_prob:',avg_prob, best_mask_loss)

        return att_acc, raw_poisoned_images[hit_idx], raw_images[hit_idx], best_mask_loss, best_mask_nz


    def get_arch_name(self):
        arch = None
        nconv = len(self.convs)
        if self.model_name == 'resnet':
            if self.convs[-1].in_channels > 1000:
                arch = 'wideresnet'
            else:
                arch = 'resnet'
            if nconv == 20:
                arch+='18'
            elif nconv == 36:
                arch+='34'
            elif nconv == 53:
                arch+='50'
            elif nconv == 104:
                arch+='101'
            elif nconv == 155:
                arch+='152'
            else:
                arch = None
        elif self.model_name == 'densenet':
            arch = 'densenet'+str(nconv+1)
        elif self.model_name == 'googlenet':
            arch = 'googlenet'
        elif self.model_name == 'inception3':
            arch = 'inceptionv3'
        elif 'squeezenet' in self.model_name:
            arch='squeezenet'
            if self.convs[0].out_channels==96:
                arch+='v1_0'
            elif self.convs[0].out_channels == 64:
                arch+='v1_1'
            else:
                arch = None
        elif self.model_name == 'mobilenetv2':
            arch='mobilenetv2'
        elif 'shufflenet' in self.model_name:
            arch='shufflenet'
            if self.convs[-1].in_channels == 464:
                arch+='1_0'
            elif self.convs[-1].in_channels == 704:
                arch+='1_5'
            elif self.convs[-1].in_channels == 976:
                arch+='2_0'
            else:
                arch = None
        elif self.model_name == 'vgg':
            arch='vgg'+str(nconv+3)+'bn'
        else:
            arch = None
        return arch



    def _calc_scorecam(self, input_images, ori_preds, layer_output):
        n_img = len(input_images)
        img_shape = input_images.shape[-2:]

        layer_output = np.maximum(layer_output,0.0)

        shape = layer_output.shape
        n_chnn = layer_output.shape[1]

        cam_list = np.zeros((shape[0], shape[2],shape[3]), dtype=np.float32)

        channel_importance = list()
        for i in range(n_chnn):
            cam_images = list()
            saliency_map = np.expand_dims(layer_output[:,i,:,:],axis=1)
            saliency_map_tensor = torch.from_numpy(saliency_map)
            saliency_map_tensor = F.interpolate(saliency_map_tensor, size=img_shape, mode='bilinear', align_corners=False)
            saliency_map = saliency_map_tensor.numpy()
            norm_saliency_map = utils.regularize_numpy_images(saliency_map)
            weighted_input = norm_saliency_map*input_images

            logits, preds = self._run_once_epoch(weighted_input)

            probs = list()
            for logit, lb in zip(logits, ori_preds):
                prob = softmax(logit)
                probs.append(prob[lb])
            probs = np.asarray(probs)
            rst = np.zeros(self.n_classes)
            for lb in range(self.n_classes):
                idx = (ori_preds==lb)
                if (np.sum(idx) == 0):
                    continue
                rst[lb] = np.mean(probs[idx])
            channel_importance.append(rst)

            w_list = probs.reshape((n_img,1,1))
            cam_list = cam_list + w_list*layer_output[:,i,:,:]

        channel_importance = np.asarray(channel_importance)
        rst_cam_list = utils.regularize_numpy_images(cam_list)

        return channel_importance, rst_cam_list


    def calc_images_centers(self, images, labels, best_child=None):
        img_shape = images.shape[-2:]

        if best_child is None:
            child_list = sorted(list(set(self.md_child)))
            child_list.reverse()
            n_child = len(child_list)
            min_chnn = np.inf
            for i in range(min(n_child, SELECT_LAYER)):
                k = child_list[i]
                _chnn = self.child_outputs[k].shape[1]
                if _chnn == self.n_classes:
                    continue
                if _chnn < min_chnn:
                    min_chnn = _chnn
                    best_child = k

        min_chnn = self.child_outputs[best_child].shape[1]

        print('calc_images_centers:','use child %d with %d channels'%(best_child, min_chnn))
        model = self._get_partial_model(st_child=0, ed_child=best_child+1)
        layer_output = self._run_once_epoch_with_model(images, model)
        print('calc_images_centers:','use child %d with output shape'%(best_child), layer_output.shape)

        _, rst_cam_list = self._calc_scorecam(images, labels, layer_output)

        trimed_cam = np.maximum(rst_cam_list-0.618,0)
        #trimed_cam = np.maximum(rst_cam_list,0)

        expanded_cam = np.expand_dims(trimed_cam,axis=1)
        cam_tensor = torch.from_numpy(expanded_cam)
        cam_tensor = F.interpolate(cam_tensor, size=img_shape, mode='bilinear', align_corners=False)
        images_cam = cam_tensor.numpy()

        row_coor = np.asarray(list(range(img_shape[0])))
        row_coor = np.reshape(row_coor, (1,1,img_shape[0],1))
        col_coor = np.asarray(list(range(img_shape[1])))
        col_coor = np.reshape(col_coor, (1,1,1,img_shape[1]))

        multi_w = images_cam
        div_sum = np.sum(multi_w,axis=(1,2,3))
        dx = np.sum(row_coor*multi_w,axis=(1,2,3))/div_sum
        dy = np.sum(col_coor*multi_w,axis=(1,2,3))/div_sum
        center_coor = np.asarray([dx,dy])
        center_coor = np.transpose(center_coor,(1,0))

        ''' #test
        print(labels)
        from misc_functions import save_image
        for z,cam in enumerate(images_cam):
            cam = np.uint8(cam*255)
            save_image(cam,'%d_haha.jpg'%z)
            img = np.uint8(images[z]*255)
            save_image(img,'%d_lala.jpg'%z)
        exit(0)
        #'''
        return center_coor


    def calc_importance(self, layer_k, test_conv=False):
        if test_conv:
            layer_output = self.ouptuts[layer_k]
        else:
            layer_output = self.child_outputs[layer_k]
        channel_importance, _ = self._calc_scorecam(self.images, self.preds_init, layer_output)

        print(np.argmax(channel_importance,axis=1))
        print(np.max(channel_importance,axis=1))
        
        thr = 0.9
        lb_matters=list()
        for lb in range(self.n_classes):
            lb_matters.append([])
        for i in range(n_chnn):
            if np.max(channel_importance[i]) < thr:
                continue
            for lb in range(self.n_classes):
                if channel_importance[i][lb] > thr:
                    lb_matters[lb].append(i)
        for lb in range(self.n_classes):
            print(lb, lb_matters[lb])
        
        return channel_importance, lb_matters


    def centralize_images(self, images, center_coor):
        h, w = images.shape[-2], images.shape[-1]
        cx, cy = h//2, w//2
        for z, coor in enumerate(center_coor):
            dx, dy = int(cx-coor[0]), int(cy-coor[1])
            img = images[z]
            img = np.roll(img,dx,axis=1)
            img = np.roll(img,dy,axis=2)
            images[z] = img

        return images


    def reverse_squeezenet(self, param):
        print(param)
        def reverse_Fire(md, output, from_conv=1):
            shape = output.shape
            if from_conv > 0:
                f_half = output[:,:shape[1]//2]
                s_half = output[:,shape[1]//2:]
                f_input = self.reverse_conv_input(md.expand1x1, f_half)
                s_input = self.reverse_conv_input(md.expand3x3, s_half)
                avg_input = (f_input+s_input)/2
                rst = self.reverse_conv_input(md.squeeze, avg_input)
            elif from_conv == 0:
                rst = self.reverse_conv_input(md.squeeze, output)
            return rst

        k,i,test_v = param[0], param[1], param[2]
        ct = 0
        for j in range(len(self.md_child)):
            if j==k:
                break
            if self.md_child[j] == self.md_child[k]:
                ct += 1



        for z,o in enumerate(self.outputs):
            print(z, o.shape)

        output = self.outputs[k]
        output[:,i] = test_v
        if ct==0:
            output_feed = output
            _input = reverse_Fire(self.childs[self.md_child[k]], output_feed, ct)
        elif ct==1:
            output_sec = self.outputs[from_conv+1]
            output_feed = np.concatenate([output,output_sec],axis=1)
            _input = reverse_Fire(self.childs[self.md_child[k]], output_feed, ct)
        elif ct == 2:
            output_pre = self.outputs[from_conv-1]
            output_feed = np.concatenate([output_pre, output], axis=1)
            _input = reverse_Fire(self.childs[self.md_child[k]], output_feed, ct)

        cut_child = self.md_child[k]-1
        while cut_child >= 0:
            md = self.childs[cut_child]
            md_name = type(md).__name__
            if md_name=='Fire':
                _input = reverse_Fire(md, _input)
            elif md_name=='Conv2d':
                _input = self.reverse_conv_input(md, _input)
            elif md_name=='MaxPool2d':
                _input = self.reverse_maxpool2d_input(md, _input)
            else:
                pass
            cut_child -= 1


        return _input


    def _expand_union_size(self, size):
        if type(size) is int:
            return (size,size)
        if type(size) is tuple:
            return size
        return None
            
    def reverse_maxpool2d_input(self, maxpool_md, output):
        kernel_size = self._expand_union_size(maxpool_md.kernel_size)
        dilation= self._expand_union_size(maxpool_md.dilation)
        padding= self._expand_union_size(maxpool_md.padding)
        stride = self._expand_union_size(maxpool_md.stride)

        shape = output.shape
        hout = shape[2]
        wout = shape[3]

        hin = (hout-1)*stride[0]+1+dilation[0]*(kernel_size[0]-1)-2*padding[0]
        win = (wout-1)*stride[1]+1+dilation[1]*(kernel_size[1]-1)-2*padding[1]

        print(maxpool_md)
        rst = F.upsample_bilinear(torch.from_numpy(output),size=(hin,win))

        return rst.cpu().numpy()


    def reverse_conv_input(self, conv_md, output):
        kernel_size = conv_md.kernel_size
        dilation=conv_md.dilation
        padding=conv_md.padding
        stride = conv_md.stride
        cin = conv_md.in_channels
        cout = conv_md.out_channels

        shape = output.shape
        hout = shape[2]
        wout = shape[3]
        hxw = hout*wout

        hin = (hout-1)*stride[0]+1+dilation[0]*(kernel_size[0]-1)-2*padding[0]
        win = (wout-1)*stride[1]+1+dilation[1]*(kernel_size[1]-1)-2*padding[1]

        weights_list =list()
        bias = None
        for p in conv_md.parameters():
            weights_list.append(p)
        if len(weights_list) > 1:
            bias = weights_list[1].detach().cpu().numpy()
            bias = np.expand_dims(bias,-1)
        kernel = weights_list[0]
        kxk = kernel_size[0]*kernel_size[1]
        kernel = torch.reshape(kernel, (cout,cin*kxk))

        prefix = torch.pinverse(kernel)
        prefix = prefix.detach().cpu().numpy()

        unfolded_input = list()
        if conv_md.groups > 1:
            for o in output:
                g_chnn = cout//conv_md.groups
                o_list = list()
                for g in conv_md.groups:
                    oo = o[g*g_chnn:g*g_chnn+g_chnn]
                    oo = np.reshape(oo,(cout,hxw))
                    if bias is not None:
                        oo = oo-bias
                    in_mat = np.matmul(prefix,oo)
                    o_list.append(in_mat)
                o_list = np.concatenate(o_list)
                unfolded_input.append(o_list)
        else:
            for o in output:
                o = np.reshape(o,(cout,hxw))
                if bias is not None:
                    o = o-bias
                in_mat = np.matmul(prefix,o)
                unfolded_input.append(in_mat)
        unfolded_input = np.asarray(unfolded_input)

        fold_fn = torch.nn.Fold((hin,win), kernel_size=kernel_size, dilation=dilation, padding=padding, stride=stride)
        folded_input = fold_fn(torch.from_numpy(unfolded_input))

        output_ones = torch.ones(unfolded_input.shape)
        folded_output_ones = fold_fn(output_ones)

        rst = folded_input/folded_output_ones
        #print('revserse', conv_md, rst.shape)

        return rst.cpu().numpy()


    def select_top_pairs(self, candi_mat):
        mc = candi_mat.flatten()
        morder = np.argsort(mc)
        nm = len(morder)
        n_try = min(PAIR_CANDI_NUM,self.n_classes)
        use_lb = np.zeros(self.n_classes,dtype=np.int32)
        pair_candi = list()
        for i in range(nm):
            z = morder[nm-1-i]
            _s, _t = z//self.n_classes, z%self.n_classes
            if _s == _t or candi_mat[_s,_t] == 0:
                continue
            if use_lb[_t] > 0:
                continue
            use_lb[_t] += 1
            pair_candi.append((_s, _t))
            if np.sum(use_lb) >= n_try:
                break

        return pair_candi


    def lrp_detection(self, candi_mat, global_neuron_dict, test_conv):
        print('candi_mat', candi_mat)
        pair_candi = self.select_top_pairs(candi_mat)
        print('selected pairs', pair_candi)

        candi_key = list()
        for _pair in pair_candi:
            _candi = list()
            for key in global_neuron_dict:
                if global_neuron_dict[key] == _pair:
                    _candi.append(key)
            _candi = sorted(_candi, key=lambda x:x[2])
            candi_key.extend(_candi[:HEATMAP_NEURON_NUM])

        print(candi_key)

        self._clear_modify_hooks()

        if len(candi_key) > 0:
            interest_layers = [x[0] for x in candi_key]
            the_layer = max(set(interest_layers),key=interest_layers.count)

        heatmap_list = list()
        for key in candi_key:
            _slb, _tlb = global_neuron_dict[key]

            indices = self.preds_all == _slb
            aimg = self.images_all[indices]
            apred = self.preds_all[indices]
            aimg = aimg[:HEATMAP_PER_NEURON]
            apred = apred[:HEATMAP_PER_NEURON]
            print(aimg.shape)

            ''' #centralization
            center_coor = self.calc_images_centers(aimg, apred, the_layer)
            aimg = self.centralize_images(aimg, center_coor)
            #'''

            lrp = LRP(self.model)

            if test_conv:
                md = self.convs[key[0]]
            else:
                md = self.childs[key[0]]
            heatmaps = lrp.interpret(aimg, (md, key[1], key[2]))
            print(heatmaps.shape, np.sum(heatmaps))
            maxv = np.max(heatmaps,axis=(1,2),keepdims=True)
            heatmaps /= maxv

            #''' #minus original heatmaps
            o_heatmaps = lrp.interpret(aimg, None)
            print(np.sum(o_heatmaps))
            o_maxv = np.max(o_heatmaps,axis=(1,2),keepdims=True)
            o_heatmaps /= o_maxv
            heatmaps += o_heatmaps
            #'''

            maxv = np.max(heatmaps,axis=(1,2),keepdims=True)
            heatmaps /= maxv

            #heatmaps = np.sum(heatmaps,axis=0,keepdims=True)
            #exit(0)


            '''
            from LRP.lrp import LRP
            lrp = LRP(self.model, 'z_plus', input_lowest=0)
            in_tensor = torch.FloatTensor(aimg)
            heatmaps = lrp.relprop(in_tensor.cuda())
            heatmaps = heatmaps.detach().cpu().numpy()
            heatmaps = np.sum(heatmaps, axis=1)
            #'''

            heatmap_list.append(heatmaps)


        if len(heatmap_list) > 0:
            heatmap_list = np.concatenate(heatmap_list)

        return heatmap_list


    def abs_reverse(self, candi_mat, global_chnn_rst, test_conv):

        pair_candi = self.select_top_pairs(candi_mat)
        neuron_candi = self.select_candi(global_chnn_rst)

        print(candi_mat)
        print(pair_candi)
        print(neuron_candi)

        selected_srclb = list()
        for pair in pair_candi:
            selected_srclb.append(pair[0])
        selected_srclb = list(set(selected_srclb))
        print('selected_srclb', selected_srclb)

        interest_layers = [x[0] for x in neuron_candi]
        the_layer = max(set(interest_layers),key=interest_layers.count)

        images = list()
        labels = list()
        raw_images = list()
        for lb in selected_srclb:
            idx = self.preds_all==lb
            images.append(self.images_all[idx])
            labels.append(self.preds_all[idx])
            raw_images.append(self.raw_images_all[idx])
        images = np.concatenate(images)
        labels = np.concatenate(labels)
        raw_images = np.concatenate(raw_images)

        ''' #false positive high
        center_coor = self.calc_images_centers(images, labels, the_layer)
        centralized_raw_images = self.centralize_images(raw_images, center_coor)
        print('centralized images shape', centralized_raw_images.shape)
        #'''

        global_att_acc = 0
        global_mask_loss = 20000
        global_mask_nz = 20000
        global_poisoned_images = None
        global_benign_images = None
        global_pair = None

        ans = 0
        reverse_start = time.time()
        for _ in range(MULTI_START):
            for pair in pair_candi:
                for neuron in neuron_candi:
                    k,i,t_lb,_ = neuron
                    if t_lb == pair[1]:
                        break

                att_acc, poisoned_images, benign_images, best_mask_loss, best_mask_nz = self.reverse_trigger((k,i), pair, (raw_images, labels), test_conv=test_conv)

                if poisoned_images is None:
                    continue
                print(poisoned_images.shape)
                print(pair, att_acc, best_mask_loss, best_mask_nz)
                coef = min(MAX_TRIGGER_SIZE/best_mask_nz,1.0)
                if att_acc*coef > ans:
                    ans = att_acc*coef
                    global_att_acc = att_acc
                    global_mask_loss = best_mask_loss
                    global_mask_nz = best_mask_nz
                    global_pair = pair
                    global_poisoned_images = poisoned_images
                    global_benign_images = benign_images
                    print('update ans to',ans)
                if ans > 0.88:
                    break
            if ans > 0.88:
                break
        reverse_stop = time.time()
        if global_pair is not None:
            utils.save_poisoned_images(global_pair, global_poisoned_images, global_benign_images)
            print('%d->%d'%(global_pair[0],global_pair[1]),'global_att_acc',global_att_acc,'global_mask_loss',global_mask_loss, 'global_mask_nz',global_mask_nz)

        reversion_time = reverse_stop-reverse_start
        print('Time used to reverse trigger: ', reversion_time)

        return ans


    def analyse(self, all_X, all_Y):
        #order = np.random.permutation(all_Y.shape[0])
        #all_X = all_X[order]
        #all_Y = all_Y[order]
        self.get_init_values(all_X, all_Y)

        #utils.save_pkl_results(self.mean_channel_max, 'poisoned_mean_channel_max','.')
        #exit(0)

        self.clear_hook_trigger()
        self.modify_hook_handles = list()
        for k,md in enumerate(self.convs):
            self.modify_hook_handles.append(md.register_forward_hook(self.get_modify_hook(k)))

        '''
        self.record_reprs = True
        self.register_representation_record_hook()
        self.reprs = list()
        self._run_once_epoch(self.images_all)
        self.record_reprs = False
        self.good_repr = np.concatenate(self.reprs)
        self.good_repr = self.regular_features(self.good_repr)
        '''

        #start = timeit.default_timer()
        start = time.time()

        run_ct = 0
        self.dealer = SCAn()
        sc_list = list()
        n_conv = len(self.convs)

        tgt_ct = [0]*self.n_classes
        tgt_list = list()
        candi_list = list()

        candi_ct = list()
        candi_mat = np.zeros([self.n_classes, self.n_classes], dtype=np.int32)

        count_k = 0

        test_conv=False
        layer_list = list()
        child_list = sorted(set(self.md_child))
        child_list.reverse()
        if self.arch_name == 'mobilenetv2':
            child_list = child_list[-11:]
        for k in child_list:
            if self.child_outputs[k].shape[1] == self.n_classes:
                continue
            layer_list.append(k)
            if len(layer_list) >= SELECT_LAYER:
                break

        #test_conv = True
        #layer_list = list(range(n_conv-10,n_conv))

        ct_sum = np.zeros(self.n_classes)

        if test_conv:
            _md_list = self.convs
        else:
            _md_list = self.childs


        global_chnn_rst = dict()
        global_neuron_dict = dict()
        for k in layer_list:
            #if k != -1:
            #if k < select_layer:
            #    continue

            if not test_conv:
                print('child:', k, 'n_chnn:', self.child_outputs[k].shape[1])
                print('abs selection test_conv',test_conv)
                chnn_rst, lb_mat, neuron_dict = self.test_layer(k)

                candi_mat = candi_mat+lb_mat
                global_chnn_rst.update(chnn_rst)
                global_neuron_dict.update(neuron_dict)

                continue

            #'''

            count_k += 1

            shape = self.outputs[k].shape
            n_chnn = shape[1]
            tmax = self.tensor_max[k]
            tmean = self.tensor_mean[k]

            print('conv: ', k, shape, tmax, tmean)

            weight_list = list()
            for p in self.convs[k].parameters():
                weight_list.append(p.cpu().detach().numpy())
            weights = weight_list[0]

            p_weights = (weights>0)
            n_weights = np.logical_not(p_weights)
            p_weights_sum = np.sum(weights*p_weights,(2,3))
            n_weights_sum = np.sum(weights*n_weights,(2,3))

            if self.convs[k].groups > 1:
                o_max = list()
                g = self.convs[k].groups
                inc = self.convs[k].in_channels
                ouc = self.convs[k].out_channels

                in_d = inc//g
                ou_d = ouc//g
                for i in range(g):
                    max_mat = self.channel_in_max[k][i*in_d:i*in_d+in_d]
                    min_mat = self.channel_in_min[k][i*in_d:i*in_d+in_d]
                    max_mat = np.maximum(max_mat,0)
                    min_mat = np.minimum(min_mat,0)
                    p_wet_mat = p_weights_sum[i*ou_d:i*ou_d+ou_d]
                    n_wet_mat = n_weights_sum[i*ou_d:i*ou_d+ou_d]

                    o_max.append(np.matmul(p_wet_mat, max_mat)+np.matmul(n_wet_mat,min_mat))
                o_max = np.concatenate(o_max)
            else:
                max_mat = self.channel_in_max[k]
                min_mat = self.channel_in_min[k]
                max_mat = np.maximum(max_mat,0)
                min_mat = np.minimum(min_mat,0)
                o_max = np.matmul(p_weights_sum, max_mat)+np.matmul(n_weights_sum,min_mat)

            if len(weight_list) > 1:
                o_max += weight_list[1]

            #'''
            print('abs selection test_conv',test_conv)
            chnn_rst, lb_mat, neuron_dict = self.test_layer(k,test_conv=test_conv, test_maxv=o_max)

            candi_mat = candi_mat+lb_mat
            global_chnn_rst.update(chnn_rst)
            global_neuron_dict.update(neuron_dict)
            print(len(global_chnn_rst))

            continue
            #'''



            self.calc_importance(k)
            exit(0)

            this_candi = list()
            for i in range(n_chnn):
                if i!=50:
                    continue
                run_ct += 1
                tv = np.max(self.lb_channel_max[k][:,i])

                #test_v = min(o_max[i], tv*2.0)*1.0
                test_v = o_max[i]*1.0

                self.start_record_conv_layers()
                f_pair, s_pair = self.test_one_channel(k,i, test_v, this_candi)
                print(i,f_pair)
                self.stop_record_conv_layers()

                '''
                #outputs = self.conv_outputs[22][:,31,:,:]
                outputs = self.conv_outputs[19][:,44,:,:]
                outputs = np.maximum(outputs,0)
                self.save_images(outputs)
                exit(0)
                #'''

            print(len(this_candi)/64)

            return len(this_candi)/64



            candi_ct.append(0)

            tgt_ct = np.zeros(self.n_classes)

            zz = 0
            rest_candi = list()
            ct_mat = np.zeros((self.n_classes,self.n_classes))
            for param, pair in this_candi:
                #prob_diff, t_lb = self.check_neuron(param,pair,o_max[param[1]])
                prob_diff, t_lb = self.check_neuron(param,pair,param[2])

                zz += 1
                print(param, prob_diff, '(%d vs %d)'%(t_lb, self.channel_lb[k][param[1]]))
                if prob_diff < EPS:
                    continue

                #rv = self.search_lower_bound(param, t_lb, o_max[param[1]])
                rv, s_lb = self.search_lower_bound(param, t_lb, param[2])
                ct_mat[s_lb,t_lb] += 1
                if rv < EPS:
                    continue

                _param = (param[0],param[1],rv*1.0)
                candi_list.append((_param, pair))
                rest_candi.append((_param,pair))

                #'''
                #candi_mat[pair[0]][pair[1]] += 1
                #candi_ct[-1] += 1

                tgt_ct[t_lb] += 1
                #tgt_list.append(pair[1])
                #'''

            if np.sum(tgt_ct) > 0:
                print('tgt_ct', tgt_ct/np.sum(tgt_ct))

            if np.sum(ct_mat) > 0:
                ct_mat /= np.sum(ct_mat)
                ct_cs = np.sum(ct_mat,0)
                ct_rs = np.sum(ct_mat,1)
                print('column sum (tgt):',np.argmax(ct_cs),ct_cs)
                print('row sum (src):',np.argmax(ct_rs),ct_rs)


            #''' #dummy check
            fn = 'id-00000369_l22_out.npy'
            load_data = np.load(fn)
            max_ans = 0
            diff = np.zeros(self.n_classes)
            _ct = np.zeros(self.n_classes)
            for i in range(n_chnn):
                test_v = load_data[3,6,:,:]
                #test_v = self.dummy_data[0,i,:,:]

                #self.guided_grad_test((22,7,test_v),(5,4))
                self.guided_grad_test((-1,-1,-1),(5,4))

                '''
                self.clear_hook_trigger()
                self.set_hook_trigger(k,i,test_v)
                logits, preds = self._run_once_epoch(self.images_all)
                self.clear_hook_trigger()

                f_pair, s_pair, _, _ = self._check_preds_backdoor(self.preds_all, logits)
                #'''
                f_pair, s_pair = self.test_one_channel(k,i, test_v, this_candi)
                lb = f_pair[0]
                _ct[lb] += 1
                diff[lb] += f_pair[1]-s_pair[1]
                if f_pair[1] > 0.5:
                    max_ans += 1
                print((k,i), f_pair,s_pair, (np.argmax(self.lb_channel_mean[k][:,i]), np.max(self.lb_channel_mean[k][:,i])), self.lb_channel_mean[k][f_pair[0],i])


                f_pair, s_pair = self.test_one_channel(k,i, o_max[i]*1.0, this_candi)

                print((k,i), f_pair,s_pair, (np.argmax(self.lb_channel_mean[k][:,i]), np.max(self.lb_channel_mean[k][:,i])), self.lb_channel_mean[k][f_pair[0],i])

                self.zero_test(k,i)

            for lb in range(len(_ct)):
                if _ct[lb] == 0:
                    continue
                diff[lb] /= _ct[lb]
            print(diff)
            #'''


        stop = time.time()
        selection_time = stop-start
        print('Time used to select neuron: ', selection_time)


        #''' #try lrp detection
        heatmaps = self.lrp_detection(candi_mat, global_neuron_dict, test_conv)
        if len(heatmaps) == 0:
            return 0
        print('done heatmaps', heatmaps.shape)

        mc = candi_mat.flatten()
        morder = np.argsort(mc)
        morder = np.flip(morder)

        base = (mc[morder[0]]+mc[morder[1]])/np.sum(mc)
        print(base)
        base = np.minimum(base/0.5, 1)*0.5


        print('save heatmaps png to', self.scratch_folder)
        for z,hm in enumerate(heatmaps):
            _p = os.path.join(self.scratch_folder, 'haha_%d'%z)
            utils.demo_heatmap(hm, _p)

        hmc = HeatMap_Classifier(CLASSIFIER_MODELPATH)
        #hmc = HeatMap_Classifier('model2.pt')
        y = hmc.predict_folder(self.scratch_folder)

        y = np.sort(y)
        print(y)
        return np.mean(y)*0.5+base

        #'''


        ''' #try abs reverse
        return self.abs_reverse(candi_mat, global_chnn_rst, test_conv)
        #'''

        #''' #reverse trigger
        fn = 'id-00000369_l22_out.npy'
        load_data = np.load(fn)
        test_v = load_data[3,6,:,:]
        param = (13,34,3) #for 555
        #param = (19,59,14) #for 555
        #hahav = test_v*(517/np.max(test_v))
        #param = (22,7,1000) # for 555
        pair = (5,4)
        self.clear_hook_trigger() 
        self.set_hook_trigger(param[0],param[1],param[2])
        print(self.hook_layer)
        print(self.hook_channels)
        print(self.hook_values)
        logits, preds = self._run_once_epoch(self.images_all)
        probs = list()
        for logit in logits:
            probs.append(softmax(logit))
        probs = np.asarray(probs)
        probs_mean = np.mean(probs,0)
        print(np.argmax(probs_mean), probs_mean)
        print(preds)

        att_acc, poisoned_images, benign_images, best_mask_loss, best_mask_nz = self.reverse_trigger(param, pair)
        print(poisoned_images.shape)
        print(pair, att_acc, best_mask_loss)
        utils.save_poisoned_images(pair, poisoned_images, benign_images)
        #'''

        print('============================')
        print(ct_sum)
        print(np.sum(ct_sum))
        ct_sum /= np.sum(ct_sum)
        order = np.argsort(ct_sum)
        order = np.flip(order) 
        ct_sum = ct_sum*1000
        ct_sum = ct_sum.astype(np.int32)
        ct_sum = ct_sum/1000

        print(ct_sum)
        for o in order:
            print(o,ct_sum[o])
        return np.max(ct_sum)
        




        print(candi_ct)
        #stop = timeit.default_timer()
        stop = time.time()
        print('Time used to select neuron: ', stop-start)

        #out_data = {'candi_ct':candi_ct, 'candi_mat':candi_mat}
        #utils.save_pkl_results(out_data)

        if self.svm_model is not None:
            print(candi_ct)
            sc = np.sum(self.svm_model.coef_[0]*candi_ct)+self.svm_model.intercept_
            alpha, beta = self.loss_model[self.arch_name]
            p = sc*alpha+beta
            p = 1.0/(1.0+np.exp(-p))
            print(sc, p)

            return p[0]

        '''
        param = (20, 138, 3.7742099)
        pair = (11,13)
        k,i,test_v = param[0], param[1], param[2]
        mx_list = list()
        for lb in range(self.n_classes):
            mx_list.append(self.lb_channel_max[k][lb][i])
        mx_list = np.asarray(mx_list)
        print(mx_list)

        att_acc, poisoned_images, benign_images = self.reverse_trigger(param, pair)
        print('recovered trigger attack acc: ', att_acc)
        utils.save_poisoned_images(pair, poisoned_images, benign_images)

        exit(0)
        #'''


        print(tgt_ct)
        tgt_ct /= np.sum(tgt_ct)
        print(tgt_ct)
        sorted_tgt = np.sort(tgt_ct)
        #if sorted_tgt[-1]-sorted_tgt[-2] < 0.1:
        #    return 0
        tgt_lb = np.argmax(tgt_ct)


        _sc_list = list()
        _candi_list = list()
        for tgt, sc, candi in zip(tgt_list, sc_list, candi_list):
            if tgt != tgt_lb:
                continue
            _sc_list.append(sc)
            _candi_list.append(candi)

        max_id = np.argmax(_sc_list)
        param, pair = _candi_list[max_id]
        print(param, pair)

        acc_list = [1.0]*self.n_classes
        mask_loss_list = [0]*self.n_classes
        for lb in range(self.n_classes):
            if lb == pair[1]:
                continue
            _pair = (lb,pair[1])
            att_acc, poisoned_images, benign_images, best_mask_loss, best_mask_nz = self.reverse_trigger(param, _pair)
            acc_list[lb] = att_acc
            mask_loss_list[lb] = best_mask_loss
        print(acc_list)
        print(mask_loss_list)
        print('recovered trigger attack acc: ', att_acc)
        if (acc_list[pair[0]] < 0.99):
            return 0

        '''
        print(poisoned_images.shape)
        print(param, pair)
        utils.save_poisoned_images(pair, poisoned_images, benign_images)
        #'''

        return self.score_to_prob(1.0/np.max(mask_loss_list))


class Reverser:
    def __init__(self, model, param, pair, md_list):
        self.model = model
        self.param = param
        self.layer_k, self.channel_i = param[0], param[1]
        self.src_lb, self.tgt_lb = pair

        self.convs = list()
        self.relus = list()
        mds = list(self.model.modules())
        for md in mds:
            na = type(md).__name__
            if na == 'Conv2d':
                self.convs.append(md)
            elif na == 'ReLU':
                self.relus.append(md)
        self.hook_handle = md_list[self.layer_k].register_forward_hook(self.get_hook())

        self.ssim_loss_fn = pytorch_ssim.SSIM()

        self.epsilon = 1e-6
        self.keep_ratio = 0
        self.init_lr = 0.01


    def get_hook(self):
        def hook(model, input, output):
            if type(input) is tuple:
                input = input[0]
            if type(output) is tuple:
                output = output[0]
            self.current_input = input
            self.current_output = output
        return hook

    def get_current_output(self):
        return self.current_output
        #return self.convs[self.layer_k](self.current_input)


    def run_model(self, input_raw_tensor):
        #'''
        _min_values = torch.min(torch.flatten(input_raw_tensor,start_dim=1), dim=1, keepdim=True).values
        _min_values = _min_values.unsqueeze(-1)
        _min_values = _min_values.unsqueeze(-1)
        x_tensor = input_raw_tensor - _min_values

        _max_values = torch.max(torch.flatten(x_tensor,start_dim=1), dim=1, keepdim=True).values
        _max_values = _max_values.unsqueeze(-1)
        _max_values = _max_values.unsqueeze(-1)
        x_tensor = x_tensor / _max_values
        #'''
        #x_tensor = input_raw_tensor/255.0

        y_tensor = self.model(x_tensor)
        self.logits = y_tensor
        logits = y_tensor.cpu().detach().numpy()
        pred = np.argmax(logits,axis=1)
        att_acc = np.sum(pred==self.tgt_lb)/len(pred)
        '''
        att_acc = 0
        for logit in logits:
            prob = softmax(logit)
            att_acc += prob[self.tgt_lb]
        att_acc /= len(logits)
        '''

        return x_tensor, att_acc


    def forward(self, input_raw_tensor):
        x_adv_raw_tensor = ((1-self.mask_tensor) * input_raw_tensor +
                        self.mask_tensor * self.pattern_raw_tensor)
        self.x_adv_raw_tensor = x_adv_raw_tensor

        self.x_adv_tensor, self.att_acc = self.run_model(self.x_adv_raw_tensor)

        output_tensor = self.get_current_output()
        relu_output_tensor = F.relu(output_tensor)

        #neuron_mask = torch.zeros(output_tensor.shape).cuda()
        #neuron_mask[:,self.channel_i,:,:] = 1
        neuron_mask = self.neuron_mask

        vloss1 = torch.sum(output_tensor*neuron_mask)/torch.sum(neuron_mask)
        vloss2 = torch.sum(output_tensor*(1-neuron_mask))/torch.sum(1-neuron_mask)
        relu_loss1 = torch.sum(relu_output_tensor*neuron_mask)/torch.sum(neuron_mask)
        relu_loss2 = torch.sum(relu_output_tensor*(1-neuron_mask))/torch.sum(1-neuron_mask)


        self.channel_loss = -vloss1-relu_loss1+1e-4*(vloss2+relu_loss2)
      
        self.mask_loss = torch.sum(self.mask_tensor)
        mask_nz = torch.sum(torch.gt(self.mask_tensor,1e-2))
        mask_cond1 = torch.gt(mask_nz, MAX_TRIGGER_SIZE)
        mask_cond2 = torch.gt(mask_nz, MAX_TRIGGER_SIZE*1.2)
        mask_add_loss = torch.where(mask_cond1, torch.where(mask_cond2, 100*self.mask_loss, 50*self.mask_loss), 0.01*self.mask_loss)

        #self.ssim_loss = self.ssim_loss_fn(self.x_adv_tensor, self.init_image_tensor)
        self.ssim_loss = torch.from_numpy(np.asarray(0.0,dtype=np.float32))

        logits_loss = torch.sum(self.logits[:,self.tgt_lb])

        #self.loss = self.keep_ratio*self.mask_loss + self.channel_loss - logits_loss
        self.loss = self.keep_ratio*mask_add_loss + self.channel_loss - logits_loss

        #self.loss = self.channel_loss

        return self.channel_loss.cpu().detach().numpy(), \
               self.mask_loss.cpu().detach().numpy(), \
               self.ssim_loss.cpu().detach().numpy()


    def backward(self):
        self.opt.zero_grad()
        self.loss.backward()
        #print(torch.sum(torch.abs(self.mask_tanh_tensor)))
        #print(torch.sum(torch.abs(self.pattern_tanh_tensor)))
        self.opt.step()
        self._upd_trigger()


    def _upd_trigger(self):
        self.mask_tensor = (torch.tanh(self.mask_tanh_tensor.cuda()) / 2 + 0.5)
        self.pattern_raw_tensor = ((torch.tanh(self.pattern_tanh_tensor.cuda()) / 2 + 0.5) * 255.0)

    def reverse(self, images):
        self.image_shape = images.shape[1:]
        print('reverse start from images with shape',images.shape)
        color, height, width = self.image_shape
        self.image_channel = self.image_shape[0]


        mask = np.zeros((1,1,height, width), dtype=np.float32)
        pattern = np.zeros((1,color, height, width), dtype=np.float32)

        #initialize
        mask_tanh = np.ones_like(mask)*-5
        #pattern_tanh = np.zeros_like(pattern)
        pattern_tanh = np.random.rand(*pattern.shape)*np.sqrt(1.0/256.0)
        pattern_tanh = pattern_tanh.astype(np.float32)

        self.mask_tanh_tensor = Variable(torch.from_numpy(mask_tanh), requires_grad=True)
        self.pattern_tanh_tensor = Variable(torch.from_numpy(pattern_tanh), requires_grad=True)

        self._upd_trigger()
        self.opt = torch.optim.Adam([self.pattern_tanh_tensor, self.mask_tanh_tensor], lr=self.init_lr, betas=(0.5,0.9))
        #self.opt = torch.optim.SGD([self.pattern_tanh_tensor, self.mask_tanh_tensor], lr=self.init_lr, momentum=0.9)

        self.init_image_tensor = torch.from_numpy(images).cuda()
        self.run_model(self.init_image_tensor)
        init_output = self.get_current_output().cpu().detach().numpy()
        self.init_output_tensor = torch.from_numpy(init_output).cuda()

        self.neuron_mask = torch.zeros(init_output.shape).cuda()
        self.neuron_mask[:,self.channel_i,:,:] = 1


        channel_loss, mask_loss, ssim_loss = self.forward(self.init_image_tensor)
        print('initial: channel_loss: %.2f, mask_loss: %.2f, ssim_loss: %.2f'%(channel_loss, mask_loss, ssim_loss))


        patience_iters = 10
        reset_patience = 50
        #self.init_ratio = 0.1/(224*224)
        self.init_ratio = 5
        self.ratio_up_multiplier = 0.2

        best_tanh_pattern = None
        best_tanh_mask = None
        best_mask_loss = float('inf')
        best_channel_loss = float('inf')
       
        self.keep_ratio = 0
        ratio_set_counter = 0
        ratio_up_counter = 0
        ratio_down_counter = 0
        has_up_ratio = False
        has_down_ratio = False
        stuck_flag = False

        att_acc_list = list()
        avg_att_acc_list = list()
        dif_att_acc_list = list()
        mask_loss_list = list()
        avg_mask_loss_list = list()
        dif_mask_loss_list = list()
        channel_loss_list = list()
        avg_channel_loss_list = list()
        dif_channel_loss_list = list()

        best_att_acc = 0
        updated = False
        reset_ct = 0
        ascd_slop_ct = 0
        ascd_fail_ct = 0

        max_steps = 8000

        def _reset_pattern_mask(tanh_pattern, tanh_mask):
            self.mask_tanh_tensor.data = torch.from_numpy(tanh_mask.copy())
            self.pattern_tanh_tensor.data = torch.from_numpy(tanh_pattern.copy())
            self._upd_trigger()

            att_acc_list.clear()
            avg_att_acc_list.clear()
            dif_att_acc_list.clear()
            mask_loss_list.clear()
            avg_mask_loss_list.clear()
            dif_mask_loss_list.clear()
            channel_loss_list.clear()
            avg_channel_loss_list.clear()
            dif_channel_loss_list.clear()

            self.forward(self.init_image_tensor)

        for step in range(max_steps):
            self.backward()
            channel_loss, mask_loss, ssim_loss = self.forward(self.init_image_tensor)

            #update cumulation list
            att_acc_list.append(self.att_acc)
            avg_att_acc = np.mean(att_acc_list[-5:])
            avg_att_acc_list.append(avg_att_acc)
            if len(avg_att_acc_list) > 1:
                dif_att_acc_list.append(avg_att_acc_list[-1]-avg_att_acc_list[-2])
            if len(dif_att_acc_list) > 50:
                avg_att_acc_slop = np.mean(dif_att_acc_list[-50:])
            mask_loss_list.append(mask_loss)
            avg_mask_loss = np.mean(mask_loss_list[-5:])
            avg_mask_loss_list.append(avg_mask_loss)
            if len(avg_mask_loss_list) > 1:
                dif_mask_loss_list.append(avg_mask_loss_list[-1]-avg_mask_loss_list[-2])
            if len(dif_mask_loss_list) > 50:
                avg_mask_loss_slop = np.mean(dif_mask_loss_list[-50:])
            channel_loss_list.append(channel_loss)
            avg_channel_loss = np.mean(channel_loss_list[-5:])
            avg_channel_loss_list.append(avg_channel_loss)
            if len(avg_channel_loss_list) > 1:
                dif_channel_loss_list.append(avg_channel_loss_list[-1]-avg_channel_loss_list[-2])
            if len(dif_channel_loss_list) > 50:
                avg_channel_loss_slop = np.mean(dif_channel_loss_list[-50:])


            #print out
            #if step%10 == 0:
            if step%10 < 0:
                print('step %d: channel_loss: %.2f, mask_loss: %.2f, ssim_loss: %.2f, att_acc: %.2f'%(step, channel_loss, mask_loss, ssim_loss, avg_att_acc))
                print(self.keep_ratio)

            #update best pattern
            if self.att_acc > best_att_acc+0.01:
                updated = True
                reset_ct = 0
                best_att_acc = self.att_acc
                best_tanh_mask = self.mask_tanh_tensor.cpu().detach().numpy().copy()
                best_tanh_pattern = self.pattern_tanh_tensor.cpu().detach().numpy().copy()
                best_mask_loss = mask_loss
            elif self.att_acc > max(0.01,best_att_acc-0.01) and mask_loss < best_mask_loss:
                updated = True
                best_tanh_mask = self.mask_tanh_tensor.cpu().detach().numpy().copy()
                best_tanh_pattern = self.pattern_tanh_tensor.cpu().detach().numpy().copy()
                best_mask_loss = mask_loss


            #warm up
            if len(att_acc_list) < 100:
                continue


            if self.keep_ratio < 1e-7:
                if best_att_acc < EPS and step > 500:
                    print('early stop by fail')
                    break
                #if updated and abs(avg_channel_loss_slop) < EPS:
                #    reset_ct += 1
                if avg_mask_loss > 1e4:
                    reset_ct += 1
                if step > 1000:
                    reset_ct += 1
                if best_att_acc > 0.99 and avg_att_acc_slop < EPS:
                    reset_ct += 1
            else:
                if avg_att_acc < best_att_acc-0.01:
                    #reset_ct = reset_patience
                    reset_ct += 1
                elif avg_mask_loss_slop > EPS:
                    reset_ct += 1
                    ascd_slop_ct += 1

            if abs(avg_mask_loss_slop) < EPS and abs(avg_channel_loss_slop) < EPS and abs(avg_att_acc_slop) < EPS:
                reset_ct += 1


            if reset_ct >= reset_patience:
                print('has updated:',updated, best_att_acc, best_mask_loss)
                if self.init_ratio < 1e-6:
                    print('early stop by reach minimum lr bound')
                    break
                elif ascd_slop_ct >= reset_patience:
                    ascd_fail_ct += 1
                else:
                    ascd_fail_ct = 0
                if ascd_fail_ct >= 3:
                    print('early stop by unable further reduce the mask size')
                    break
                reset_ct = 0
                ascd_slop_ct = 0
                _reset_pattern_mask(best_tanh_pattern, best_tanh_mask)
                self.init_ratio /= 5
                self.keep_ratio = self.init_ratio
                ratio_set_counter = 0
                ratio_up_counter = 0
                ratio_down_counter = 0
                has_up_ratio = False
                has_down_ratio = False
                updated = False
                stuck_flag = False
                print('reset mask pattern:', 'set keep_ratio to',self.keep_ratio)
                continue


            '''
            if self.att_acc > 0.99:
                ratio_up_counter += 1
            else:
                ratio_up_counter = 0

            if ratio_up_counter >= patience_iters:
                ratio_up_counter = 0
                self.keep_ratio *= (1.0+self.ratio_up_multiplier)
                #print('keep_ratio up to: {}'.format(self.keep_ratio))
                has_up_ratio = True
                if has_up_ratio and has_down_ratio:
                    self.ratio_up_multiplier *= 0.95
                    has_up_ratio = False
                    has_down_ratio = False
            elif ratio_down_counter >= patience_iters:
                ratio_down_counter = 0
                self.keep_ratio /= (1.0+self.ratio_up_multiplier)
                self.keep_ratio = max(1e-7,self.keep_ratio)
                has_down_ratio = True
                #print('keep_ratio down to: {}'.format(self.keep_ratio))
                if has_up_ratio and has_down_ratio:
                    self.ratio_up_multiplier *= 0.95
                    has_up_ratio = False
                    has_down_ratio = False
            '''


        print('best att_acc:',best_att_acc,'mask_loss:',best_mask_loss)

        if best_tanh_mask is None:
            return None, None, best_mask_loss

        best_mask = np.tanh(best_tanh_mask)/2+0.5
        best_raw_pattern = (np.tanh(best_tanh_pattern)/2+0.5)*255.0

        self.hook_handle.remove()
        self.hook_handle = None


        del self.mask_tanh_tensor
        del self.pattern_tanh_tensor
        del self.init_image_tensor
        del self.init_output_tensor
        del self.neuron_mask
        
        torch.cuda.empty_cache()

        return best_mask, best_raw_pattern, best_mask_loss



class LRP:
    def __init__(self, model):
        #the model must have no any hook.
        #the model can calc the gradients.
        #self.image_mean = torch.Tensor([0.485, 0.456, 0.406]).reshape(1,-1,1,1)
        #self.image_std  = torch.Tensor([0.229, 0.224, 0.225]).reshape(1,-1,1,1)

        self.tot_k = 0
        self.model = model

        self._lambda = 0.25
        self._epsilon = 1e-9


        self.model_name = get_model_name(model)
        print('LRP',self.model_name)
        if 'resnet' not in self.model_name and 'mobilenet' not in self.model_name:
            self.fashion = 'auto'
        else:
            self.fashion = 'manual'
        if 'densenet' in self.model_name:
            self.bn_location='before'
        else:
            self.bn_location='after'

        childs = make_childs(self.model)
        self.model = build_model_from_childs(childs)
        self.childs = childs

        '''
        for z,ch in enumerate(childs):
            print(z,ch)
        exit(0)
        #'''

        md_list = module_flatten(childs)
        self.md_list = md_list



    def init_records(self):
        self.grads = list()
        for i in range(self.tot_k):
            self.grads.append(None)


    def clear_records(self):
        while (len(self.grads)):
            _tmp = self.grads[-1]
            del _tmp
            del self.grads[-1]


    def get_lrp_forward_func(self, layer, rho):
        if isinstance(layer, torch.nn.Conv2d):
            weight, bias = self.func_apply(layer.weight, layer.bias, rho)
            func = F.conv2d
            func_args = {
                'weight': weight,
                'bias': bias,
                'stride': layer.stride,
                'padding': layer.padding,
                'dilation': layer.dilation,
                'groups': layer.groups
            }
        elif isinstance(layer, torch.nn.Linear):
            weight, bias = self.func_apply(layer.weight, layer.bias, rho)
            func = F.linear
            func_args = {
                'weight': weight,
                'bias': bias
            }
        elif isinstance(layer, torch.nn.AvgPool2d):
            func = F.avg_pool2d
            func_args = {
                'kernel_size': layer.kernel_size,
                'stride': layer.stride,
                'padding': layer.padding,
                'ceil_mode': layer.ceil_mode
            }
        elif isinstance(layer, torch.nn.MaxPool2d):
            '''
            func = F.max_pool2d
            func_args = {
                'kernel_size': layer.kernel_size,
                'stride': layer.stride,
                'padding': layer.padding,
                'dilation': layer.dilation,
                'return_indices': layer.return_indices,
                'ceil_mode': layer.ceil_mode
            }
            '''
            func = F.avg_pool2d
            func_args = {
                'kernel_size': layer.kernel_size,
                'stride': layer.stride,
                'padding': layer.padding,
                'ceil_mode': layer.ceil_mode
            }
        elif isinstance(layer, torch.nn.AdaptiveAvgPool2d):
            func = F.adaptive_avg_pool2d
            func_args = {
                'output_size': layer.output_size
            }
        elif isinstance(layer, torch.nn.BatchNorm2d):
            func = F.batch_norm
            func_args = {
                'running_mean': layer.running_mean,
                'running_var' : layer.running_var,
                'weight'      : layer.weight,
                'bias'        : layer.bias,
                'momentum'    : layer.momentum,
                'eps'         : layer.eps
            }
        elif isinstance(layer, torch.nn.ReLU):
            func = F.relu
            func_args = {}
        elif isinstance(layer, torch.nn.ReLU6):
            func = F.relu6
            func_args = {}
        else:
            raise RuntimeError('unknown layer: '+layer)

        return func, func_args


   
    def apply_lrp_func(self, layer, input, rho):
        func, func_args = self.get_lrp_forward_func(layer,rho)
        return func(input, **func_args)


    def get_tensor_backward_hook(self, layer_k):
        def hook(grad):

            bn_layer = self.bn_layer[layer_k]
            layer = self.layer_record[layer_k]
            rho, incr = self.get_functions(layer_k)

            #print('backward',layer_k,layer)

            if self.bn_location == 'before' and bn_layer is not None:
                #print('bn before')

                v = bn_layer.input_tensor
                z = self.apply_lrp_func(bn_layer, v, rho)
                if self.relu_layer[layer_k] is not None:
                    z = F.relu(z)
                z = self.apply_lrp_func(layer, z, rho)
                z = incr(z)
                #print(v.shape)
                #print(z.shape)
                grad_output = self.grads[layer_k]
                #print(grad_output.shape)

            elif self.bn_location == 'after' and bn_layer is not None:
                #print('bn after')

                v = layer.input_tensor
                z = self.apply_lrp_func(layer, v, rho)
                z = incr(z)
                if self.relu_layer[layer_k] is not None:
                    z = F.relu(z)
                z = self.apply_lrp_func(bn_layer, z, rho)
                if self.relu_layer[layer_k] is None:
                    z = F.relu(z)
                grad_output = bn_layer.haha_R

            else:
                v = layer.input_tensor
                z = self.apply_lrp_func(layer, v, rho)
                z = incr(z)
                grad_output = self.grads[layer_k]

            s = (grad_output/z).data
            (z*s).sum().backward(); c=v.grad
            relevance = (v*c).data

            #print(layer_k, torch.sum(relevance))
            grad.copy_(relevance)

            '''
            if layer_k==-1:
                ha = grad.detach().cpu().numpy()
                print('demo', layer_k, ha.shape)
                ha = np.sum(ha,axis=1)
                utils.demo_heatmap(ha[0], 'haha.png')
                exit(0)
            #'''

            del v, z, s, relevance

            return grad
        return hook

    
    def get_record_forward_hook(self, layer_k):
        def hook(model, input, output):
            if type(input) is tuple:
                if len(input) == 1:
                    input = input[0]
                else:
                    raise RuntimeError('too many input')
            #print(layer_k, model, len(input), len(output))
            model.input_tensor = torch.tensor(input.clone(), requires_grad=True)
            if self.fashion == 'manual':
                if type(output) is tuple:
                    if len(output) == 1:
                        output = output[0]
                    else:
                        raise RuntimeError('too many output')
                model.output_tensor = torch.tensor(output.clone(), requires_grad=True)
            else:
                if layer_k == 0:
                    self.hook_handles.append(input.register_hook(self.get_input_tensor_backward_hook(layer_k)))
                else:
                    self.hook_handles.append(input.register_hook(self.get_tensor_backward_hook(layer_k)))

        return hook


    def get_record_backward_hook(self, layer_k):
        def hook(module, grad_input, grad_output):
            #print('backward hook', layer_k, module)
            if type(grad_output) is tuple:
                if len(grad_output) == 1:
                    grad_output = grad_output[0]
                else:
                    raise RuntimeError('too many grad_output')

            self.grads[layer_k] = torch.tensor(grad_output.clone(), requires_grad=True)

        return hook


    def get_relu_backward_hook(self):
        def hook(module, grad_input, grad_output):
            #return None
            #print('relu')
            return grad_output
        return hook


    def get_bn_backward_copy_hook(self):
        def hook(module, grad_input, grad_output):
            #print('backward bn', module)
            if type(grad_output) is tuple:
                if len(grad_output) == 1:
                    grad_output = grad_output[0]
                else:
                    raise RuntimeError('too many grad_output')

            for x in grad_input:
                if x.shape == gard_output.shape:
                    haha = x
                    break

            haha.copy_(grad_output)
        return hook


    def get_bn_backward_hook(self, layer_k):
        def hook(module, grad_input, grad_output):
            #print(layer_k, module, len(grad_input), len(grad_output), grad_input[0].shape)
            if type(grad_output) is tuple:
                if len(grad_output) == 1:
                    grad_output = grad_output[0]
                else:
                    raise RuntimeError('too many grad_output')

            for x in grad_input:
                if x.shape == grad_output.shape:
                    haha = x
                    break

            haha.copy_(grad_output)

            module.haha_R = grad_output.detach().clone()

        return hook

    def get_input_tensor_backward_hook(self, layer_k):
        def hook(grad):
            bn_layer = self.bn_layer[layer_k]
            layer = self.layer_record[layer_k]

            if self.bn_location == 'before' and bn_layer is not None:
                #print('bn before')

                v = bn_layer.input_tensor
                lb = (torch.ones_like(v,dtype=v.dtype)*0).requires_grad_(True)
                hb = (torch.ones_like(v,dtype=v.dtype)*1).requires_grad_(True)

                z = bn_layer.forward(v)
                zl = bn_layer.forward(lb)
                zh = bn_layer.forward(hb)

                if self.relu_layer[layer_k] is not None:
                    z = F.relu(z)
                    zl = F.relu(zl)
                    zh = F.relu(zh)
                z = layer.forward(z)
                zl = self.apply_lrp_func(layer, zl, lambda p : p.clamp(min=0))
                zh = self.apply_lrp_func(layer, zh, lambda p : p.clamp(max=0))

                grad_output = self.grads[layer_k]

            elif self.bn_location == 'after' and bn_layer is not None:
                #print('bn after')

                v = layer.input_tensor
                lb = (torch.ones_like(v,dtype=v.dtype)*0).requires_grad_(True)
                hb = (torch.ones_like(v,dtype=v.dtype)*1).requires_grad_(True)

                z = layer.forward(v)
                zl = self.apply_lrp_func(layer, lb, lambda p : p.clamp(min=0))
                zh = self.apply_lrp_func(layer, hb, lambda p : p.clamp(max=0))

                if self.relu_layer[layer_k] is not None:
                    z = F.relu(z)
                    zl = F.relu(zl)
                    zh = F.relu(zh)

                z = bn_layer.forward(z)
                zl = bn_layer.forward(zl)
                zh = bn_layer.forward(zh)

                grad_output = bn_layer.haha_R

            else:
                v = layer.input_tensor
                lb = (torch.ones_like(v,dtype=v.dtype)*0).requires_grad_(True)
                hb = (torch.ones_like(v,dtype=v.dtype)*1).requires_grad_(True)

                z = layer.forward(v)
                zl = self.apply_lrp_func(layer, lb, lambda p : p.clamp(min=0))
                zh = self.apply_lrp_func(layer, hb, lambda p : p.clamp(max=0))
                grad_output = self.grads[layer_k]

            z = z-zl-zh+1e-9
            s = (grad_output/z).data
            (z*s).sum().backward(); c,cp,cm = v.grad,lb.grad,hb.grad
            #relevance = (v*c-lb*cp-hb*cm).data
            relevance = (v*c+lb*cp+hb*cm).data

            grad.copy_(relevance)

            del lb, hb, z, zl, zh, s, relevance

            return grad

        return hook


    def func_apply(self, weight, bias, func):
        weight = func(weight)
        if bias is not None:
            bias = func(bias)
        return weight, bias

    def _calc_R_for_basic_layer_list(self, v, layer_list, R, rho, incr):
        z = v
        for layer in layer_list:
            z = self.apply_lrp_func(layer,z,rho)
        z = incr(z)
        s = (R/z).data
        c = torch.autograd.grad(outputs=(z*s).sum(), inputs=v)

        if len(c) != 1:
            raise RuntimeError('bug')
        c = c[0]

        relevance = (v*c).data
        return relevance

    def _calc_R_for_Sequential(self, layer, R, rho, incr, bn_location):
        layer_list = list(layer.children())
        return self.calc_R_for_layer_list(layer_list,R,rho,incr,bn_location)


    def _backprop_skip_connect(self, act0, act1, R):
        z = act0 + act1 + 1e-9
        s = (R/z).data
        R0 = act0*s
        R1 = act1*s
        return (R0,R1)

    def _calc_R_for_BasicBlock(self, layer, R, rho, incr, bn_location):
        if layer.downsample is not None:
            identity = layer.downsample.output_tensor
        else:
            identity = layer.input_tensor

        activation = layer.bn2.output_tensor
        R0, R1 = self._backprop_skip_connect(activation, identity, R)

        layer_list = [layer.conv1, layer.bn1, layer.relu, layer.conv2, layer.bn2]
        R0 = self.calc_R_for_layer_list(layer_list, R0, rho, incr, bn_location)
        if layer.downsample is not None:
            R1 = self._calc_R_for_Sequential(layer.downsample, R1, rho, incr, bn_location)

        return R0+R1

    def _calc_R_for_Bottleneck(self, layer, R, rho, incr, bn_location):
        if layer.downsample is not None:
            identity = layer.downsample.output_tensor
        else:
            identity = layer.input_tensor

        activation = layer.bn3.output_tensor
        R0, R1 = self._backprop_skip_connect(activation, identity, R)

        layer_list = [layer.conv1, layer.bn1, layer.relu, layer.conv2, layer.bn2, layer.relu, layer.conv3, layer.bn3]
        R0 = self.calc_R_for_layer_list(layer_list, R0, rho, incr, bn_location)
        if layer.downsample is not None:
            R1 = self._calc_R_for_Sequential(layer.downsample, R1, rho, incr, bn_location)

        return R0+R1

    def _calc_R_for_ConvBNReLU(self, layer, R, rho, incr, bn_location):
        return self._calc_R_for_Sequential(layer,R,rho,incr,bn_location)

    def _calc_R_for_InvertedResidual(self, layer, R, rho, incr, bn_location):
        if layer.use_res_connect:
            identity = layer.input_tensor
            activation = layer.conv.output_tensor
            R0, R1 = self._backprop_skip_connect(activation, identity, R)
            R0 = self._calc_R_for_Sequential(layer.conv,R0,rho,incr,bn_location)
            return R0+R1
        else:
            return self._calc_R_for_Sequential(layer.conv,R,rho,incr,bn_location)


    def calc_R_for_layer_list(self, layer_list, R, rho, incr, bn_location='after'):
        interested_names = ['Conv2d','AdaptiveAvgPool2d','AvgPool2d','MaxPool2d','Linear']
        layer_names = [type(l).__name__ for l in layer_list]
        n_layers = len(layer_list)

        i = n_layers-1
        while i>=0:
            #print('deal',i,layer_names[i])

            if (bn_location=='after' and i-2>=0) and (layer_names[i-2] in interested_names) and layer_names[i-1].startswith('ReLU') and layer_names[i]=='BatchNorm2d':
                R = self._calc_R_for_basic_layer_list(layer_list[i-2].input_tensor, layer_list[i-2:i+1], R, rho, incr)
                i-=2
            elif (bn_location=='after' and i-1>=0) and (layer_names[i-1] in interested_names) and layer_names[i]=='BatchNorm2d':
                R = self._calc_R_for_basic_layer_list(layer_list[i-1].input_tensor, layer_list[i-1:i+1], R, rho, incr)
                i-=1
            elif (bn_location=='before' and i-2>=0) and (layer_names[i] in interested_names) and layer_names[i-2]=='BatchNorm2d' and layer_names[i-1].startswith('ReLU'):
                R = self._calc_R_for_basic_layer_list(layer_list[i-2].input_tensor, layer_list[i-2:i+1], R, rho, incr)
                i-=2
            elif (bn_location=='before' and i-1>=0) and (layer_names[i] in interested_names) and layer_names[i-1]=='BatchNorm2d':
                R = self._calc_R_for_basic_layer_list(layer_list[i-1].input_tensor, layer_list[i-1:i+1], R, rho, incr)
                i-=1
            elif layer_names[i]=='BatchNorm2d' or layer_names[i] in interested_names:
                R = self._calc_R_for_basic_layer_list(layer_list[i].input_tensor, layer_list[i:i+1], R, rho, incr)
            elif hasattr(self,'_calc_R_for_'+layer_names[i]):
                func = getattr(self,'_calc_R_for_'+layer_names[i])
                R = func(layer_list[i], R, rho, incr, bn_location)
            elif layer_names[i]=='Flatten':
                inp = layer_list[i].input_tensor
                R = torch.reshape(R,inp.shape)
            elif layer_names[i]=='Dropout' or layer_names[i].startswith('ReLU'):
                R = R
            else:
                raise RuntimeError('unknown layer '+layer_names[i])
                print('unknown layer:',layer_names[i])
                layer = layer_list[i]
                inp = layer.input_tensor
                oup = layer.output_tensor
                R = torch.autograd.grad(outputs=oup, inputs=inp, grad_outputs=R, allow_unused=True)
                print(inp.shape)
                print(oup.shape)
                print(len(R))
            i-=1

        return R


    def _add_hooks(self):
        if self.fashion == 'manual':
            return self._add_manual_hooks()
        else:
            return self._add_auto_hooks()

    def _add_manual_hooks(self):
        hs = list()
        for md in list(self.model.modules()):
            hs.append(md.register_forward_hook(self.get_record_forward_hook(-1)))
        return hs

    def _add_auto_hooks(self):
        self.layer_record = list()
        self.bn_layer = list()
        self.relu_layer = list()
        k = 0
        hs = list()
        for md in self.md_list:
            interested = False
            if isinstance(md, torch.nn.MaxPool2d):
                interested = True
            elif isinstance(md, torch.nn.Conv2d):
                interested = True
            elif isinstance(md, torch.nn.AvgPool2d) or isinstance(md, torch.nn.AdaptiveAvgPool2d):
                interested = True
            elif isinstance(md, torch.nn.Linear):
                interested = True
            elif isinstance(md, torch.nn.BatchNorm2d):
                interested = False
                if self.bn_location == 'after':
                    kk = k-1
                    if self.bn_layer[kk] is None:
                        self.bn_layer[kk] = md
                elif self.bn_location == 'before':
                    kk = k
                    while kk >= len(self.bn_layer):
                        self.bn_layer.append(None)
                    if self.bn_layer[kk] is not None:
                        raise RuntimeError('bug')
                    self.bn_layer[kk] = md
                else:
                    raise RuntimeError('unknowd bn_location')

                hs.append(md.register_backward_hook(self.get_bn_backward_hook(kk)))

            elif isinstance(md, torch.nn.ReLU) or isinstance(md, torch.nn.ReLU6):
                interested = False
                if self.bn_location == 'after':
                    kk = k-1
                    if self.bn_layer[kk] is None:
                        self.relu_layer[kk] = md
                elif self.bn_location == 'before':
                    kk = k
                    while kk >= len(self.relu_layer):
                        self.relu_layer.append(None)
                    if len(self.bn_layer) > kk and self.bn_layer[kk] is not None:
                        self.relu_layer[kk] = md

                hs.append(md.register_backward_hook(self.get_relu_backward_hook()))

            if interested:
                self.layer_record.append(md)
                while len(self.bn_layer) < len(self.layer_record):
                    self.bn_layer.append(None)
                while len(self.relu_layer) < len(self.layer_record):
                    self.relu_layer.append(None)

                if self.bn_location == 'before' and self.bn_layer[k] is not None:
                    hs.append(self.bn_layer[k].register_forward_hook(self.get_record_forward_hook(k)))
                else:
                    hs.append(md.register_forward_hook(self.get_record_forward_hook(k)))
                hs.append(md.register_backward_hook(self.get_record_backward_hook(k)))
                k += 1
        self.tot_k = k

        '''
        print(k)
        for z,h in enumerate(self.layer_record):
            print(z,h)
        exit(0)
        #'''

        return hs


    def get_functions(self, layer_k):
        if 'vgg' not in self.model_name:
            rho  = lambda p : p
            incr = lambda z : z+self._epsilon
            return rho, incr

        if layer_k*3 >= self.tot_k*2:
            rho  = lambda p : p
            incr = lambda z : z+self._epsilon
        elif layer_k*3 >= self.tot_k*1:
            rho  = lambda p : p
            incr = lambda z : z+self._epsilon + 0.25*((z**2).mean()**.5).data
        else:
            rho  = lambda p : p+self._lambda*p.clamp(min=0)
            incr = lambda z : z+self._epsilon
        return rho, incr


    def clear_hooks(self):
        for h in self.hook_handles:
            h.remove()
        self.hook_handles = list()


    def get_modify_hook(self, chnn_i, test_v):
        def hook(model, input, output):
            if type(output) is tuple:
                output = output[0]
            nchnn = output.shape[1]
            mask = torch.FloatTensor((1.0*(np.arange(nchnn)==chnn_i).reshape([1,nchnn,1,1])))
            mask = mask.cuda()
            output = test_v*mask+(1-mask)*output
            return output
        return hook


    def clear_assets(self):
        for md in list(self.model.modules()):
            if hasattr(md, 'input_tensor'): del md.input_tensor
            if hasattr(md, 'output_tensor'): del md.output_tensor
            if not isinstance(md, torch.nn.BatchNorm2d): continue
            if hasattr(md, 'haha_R'): del md.haha_R


    def interpret(self, images, hook_param=None):
        self.model.zero_grad()
        self.hook_handles = self._add_hooks()

        if hook_param is not None:
            md, i, v = hook_param
            self.hook_handles.append(md.register_forward_hook(self.get_modify_hook(i, v)))

        input_tensor = torch.FloatTensor(images).cuda()
        input_variable = Variable(input_tensor, requires_grad=True)

        self.init_records()

        logits = self.model(input_variable)
        n_classes, img_lb = logits.shape[1], torch.argmax(logits,1).detach().cpu().numpy()

        Ts=list()
        for lb in img_lb:
            Ts.append(torch.FloatTensor((1.0*(np.arange(n_classes)==lb).reshape([1,n_classes]))))
        Ts = torch.cat(Ts)
        T = Ts*logits.cpu().data

        if self.fashion=='manual':
            rho, incr = self.get_functions(10000)
            input_heatmap = self.calc_R_for_layer_list(self.childs, T.cuda(), rho, incr, bn_location=self.bn_location)
        else:
            loss = torch.sum(T.cuda()*logits)
            loss.backward(create_graph=True, retain_graph=True)
            input_heatmap = input_variable.grad.data

        input_heatmap = input_heatmap.detach().cpu().numpy()
        input_heatmap = input_heatmap.sum(axis=1)

        print(input_heatmap.shape)
        print(np.sum(input_heatmap, axis=(1,2)))
        #maxv = np.max(input_heatmap, axis=(1,2), keepdims=True)
        #input_heatmap /= maxv

        self.clear_records()
        self.clear_hooks()
        self.clear_assets()

        #os.system('nvidia-smi')
        del input_tensor,input_variable,logits,T
        torch.cuda.empty_cache()
        #os.system('nvidia-smi')

        return input_heatmap



class HeatMap_Classifier:
    def __init__(self, model_path):
        self.model = torch.load(model_path)
        self.model = self.model.cuda()
        self.model.eval()

    def predict(self, images):
        img_tensor = torch.Tensor(images)
        print('input image', img_tensor.shape)

        y = self.model(img_tensor.cuda())
        y = torch.softmax(y, dim=1)

        y = y.detach().cpu().numpy()
        print(y.shape)

        return y[:,1]

    def predict_folder(self, folder_path):
        import skimage.io
        import skimage.color

        img_path_list = os.listdir(folder_path)
        imgs = list()
        for f in img_path_list:
            filepath = os.path.join(folder_path,f)

            #image preprocess
            #img = cv2.imread(filepath)
            #img = np.asarray(img)
            img = skimage.io.imread(filepath)
            if img.shape[-1] > 3:
                img = skimage.color.rgba2rgb(img)
            img = np.transpose(img,(2,0,1))
            img = img/255.0

            imgs.append(img)
        imgs = np.asarray(imgs)
        return self.predict(imgs)



def process_run(k, idx, init_x, init_y, pipes, n_classes):
    pipe = pipes.pop()
    sna = SingleNeuronAnalyzer(idx, init_x, init_y, pipe, n_classes)
    sna.run()
    pipes.append(pipe)
    print((k, idx, sna.peak))
    return (k, idx, sna.peak)
    #return (idx, sna.peak, sna.x_list, sna.y_list)
