from __future__ import print_function
import os
import sys
import time

import argparse
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torchvision

import matplotlib
from scipy.ndimage.interpolation import rotate
matplotlib.use('agg')
import matplotlib.pyplot as plt
from scipy.ndimage.interpolation import rotate
from torchvision import datasets, transforms

from model import Encoder,feature_transformer

def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
         nn.init.xavier_normal_(m.weight)


def rotate_tensor(input,init_rot_range,relative_rot_range, plot=False):
    """
    Rotate the image
    Args:
        input: [N,c,h,w] **numpy** tensor
        init_rot_range:     (scalar) the range of ground truth rotation
        relative_rot_range: (scalar) the range of relative rotations
        plot: (flag)         plot the original and rotated digits
    Returns:
        outputs1: [N,c,h,w]  input rotated by offset angle
        outputs2: [N,c,h,w]  input rotated by offset angle + relative angle [0, rot_range]
        relative angele [N,1] relative angle between outputs1 and outputs 2 in radians
    """
    #Define offest angle of input
    offset_angles=init_rot_range*np.random.rand(input.shape[0])
    offset_angles=offset_angles.astype(np.float32)

    #Define relative angle
    relative_angles=relative_rot_range*np.random.rand(input.shape[0])
    relative_angles=relative_angles.astype(np.float32)


    outputs1=[]
    outputs2=[]
    for i in range(input.shape[0]):
        output1 = rotate(input[i,...], 180*offset_angles[i]/np.pi, axes=(1,2), reshape=False)
        output2 = rotate(input[i,...], 180*(offset_angles[i]+relative_angles[i])/np.pi, axes=(1,2), reshape=False)
        outputs1.append(output1)
        outputs2.append(output2)

    outputs1=np.stack(outputs1, 0)
    outputs2=np.stack(outputs2, 0)

    if plot:
        #Create of output1 and outputs1
        N=input.shape[0]
        rows=int(np.floor(N**0.5))
        cols=N//rows
        plt.figure()
        for j in range(N):
            plt.subplot(rows,cols,j+1)
            if outputs1.shape[1]>1:
                image=outputs1[j].transpose(1,2,0)
            else:
                image=outputs1[j,0]

            plt.imshow(image, cmap='gray')
            plt.grid(False)
            plt.title(r'$\theta$={:.1f}'.format(offset_angles[j]*180/np.pi), fontsize=6)
            plt.axis('off')
        #Create new figure with rotated
        plt.figure(figsize=(7,7))
        for j in range(N):
            plt.subplot(rows,cols,j+1)
            if input.shape[1]>1:
                image=outputs2[j].transpose(1,2,0)
            else:
                image=outputs2[j,0]
            plt.imshow(image, cmap='gray')
            plt.axis('off')
            plt.title(r'$\theta$={:.1f}'.format( (offset_angles[i]+relative_angles[i])*180/np.pi), fontsize=6)
            plt.grid(False)
        plt.tight_layout()      
        plt.show()

    return outputs1, outputs2, relative_angles


def save_model(args,model):
    """
    saves a checkpoint so that model weight can later be used for inference
    Args:
    model:  pytorch model
    """

    path='./model_'+args.name
    import os
    if not os.path.exists(path):
      os.mkdir(path)
    torch.save(model.state_dict(), path+'/checkpoint.pt')



def evaluate_model(args,model, device, data_loader):
    """
    Evaluate loss in subsample of data_loader
    """
    model.eval()
    with torch.no_grad():
        for data, targets in data_loader:
            # Reshape data
            data,targets,angles = rotate_tensor(data.numpy(),args.init_rot_range, args.relative_rot_range)
            targets = torch.from_numpy(targets).to(device)
            angles = torch.from_numpy(angles).to(device)
            angles = angles.view(angles.size(0), 1)
            data = torch.from_numpy(data).to(device)

            # Forward passes
            f_data=model(data) # [N,2,1,1]
            f_targets=model(targets) #[N,2,1,1]

            #Apply rotation matrix to f_data with feature transformer
            f_data_trasformed= feature_transformer(f_data,angles,device)

            #Define loss
            loss=define_loss(args,f_data_trasformed,f_targets)
            break

    return loss.cpu()


def rotation_test(args,model, device, test_loader):
    """
    Test how well the eoncoder discrimates angles
    return the average error in degrees
    """
    model.eval()
    with torch.no_grad():
        for data, targets in test_loader:
            ## Reshape data
            data,targets,angles = rotate_tensor(data.numpy(),args.init_rot_range, args.relative_rot_range)
            data = torch.from_numpy(data).to(device)
            targets = torch.from_numpy(targets).to(device)
            angles = torch.from_numpy(angles).to(device)
            angles = angles.view(angles.size(0), 1)

            # Forward passes
            f_data=model(data) # [N,2,1,1]
            f_targets=model(targets) #[N,2,1,1]

            #Get cosine similarity
            f_data=f_data.view(f_data.size(0),1,2)
            f_targets=f_targets.view(f_targets.size(0),1,2)

            cosine_similarity=nn.CosineSimilarity(dim=2)

            predicted_cosine=cosine_similarity(f_data,f_targets)

            predicted_angle=(torch.acos(predicted_cosine)).cpu()

            error=((predicted_angle-angles.cpu())*180/np.pi).numpy()

            abs_mean_error=abs(error).mean()
            error_std=error.std(ddof=1)
            break

    return abs_mean_error,error_std


def define_loss(args, x,y):
    """
    Return the loss based on the user's arguments

    Args:
        x:  [N,2,1,1]    output of encoder model
        y:  [N,2,1,1]    output of encode model
    """

    if args.loss=='forbenius':
        forb_distance=torch.nn.PairwiseDistance()
        x_polar=x.view(-1,2)
        x_polar=x/x.norm(p=2,dim=1,keepdim=True)
        y_polar=y.view(-1,2)
        y_polar=y/y.norm(p=2,dim=1,keepdim=True)
        loss=(forb_distance(x_polar,y_polar)**2).sum()

    elif args.loss=='cosine_squared':

        cosine_similarity=nn.CosineSimilarity(dim=2)
        loss=((cosine_similarity(x.view(x.size(0),1,2),y.view(y.size(0),1,2))-1.0)**2).sum()

    elif args.loss=='cosine_abs':

        cosine_similarity=nn.CosineSimilarity(dim=2)
        loss=torch.abs(cosine_similarity(x.view(x.size(0),1,2),y.view(y.size(0),1,2))-1.0).sum()

    return loss

def main():
    # Training settings
    list_of_choices=['forbenius', 'cosine_squared','cosine_abs']

    parser = argparse.ArgumentParser(description='PyTorch MNIST Example')
    parser.add_argument('--batch-size', type=int, default=64, metavar='N',
                        help='input batch size for training (default: 64)')
    parser.add_argument('--test-batch-size', type=int, default=1000, metavar='N',
                        help='input batch  rotation test (default: 1000)')
    parser.add_argument('--epochs', type=int, default=20, metavar='N',
                        help='number of epochs to train (default: 20)')
    parser.add_argument('--lr', type=float, default=0.001, metavar='LR',
                        help='learning rate (default: 0.001)')
    parser.add_argument('--momentum', type=float, default=0.9, metavar='M',
                        help='SGD momentum (default: 0.9)')
    parser.add_argument('--no-cuda', action='store_true', default=False,
                        help='disables CUDA training')
    parser.add_argument('--seed', type=int, default=1, metavar='S',
                        help='random seed (default: 1)')
    parser.add_argument('--log-interval', type=int, default=10, metavar='N',
                        help='how many batches to wait before logging training status')
    parser.add_argument('--store-interval', type=int, default=50, metavar='N',
                        help='how many batches to wait before storing training loss')
    parser.add_argument('--name', type=str, default='',
                        help='name of the run that is added to the output directory')
    parser.add_argument("--loss",dest='loss',default='forbenius',
    choices=list_of_choices, help='Decide type of loss, (forbenius) norm, difference of (cosine), (default=forbenius)')
    parser.add_argument('--init-rot-range',type=float, default=0,
                        help='Upper bound of range in degrees of initial random rotation of digits, (Default=0)')
    parser.add_argument('--relative-rot-range',type=float, default=180,
                        help='Upper bound of range in degrees of relative rotation between digits (Default=180)')

    
    args = parser.parse_args()

    args.init_rot_range=args.init_rot_range*np.pi/180
    args.relative_rot_range= args.relative_rot_range*np.pi/180
    # Create save path

    path = "./output_"+args.name
    if not os.path.exists(path):
        os.makedirs(path)

    sys.stdout.write('Start training\n')
    sys.stdout.flush()

    use_cuda = not args.no_cuda and torch.cuda.is_available()

    torch.manual_seed(args.seed)

    device = torch.device("cuda" if use_cuda else "cpu")

    # Set up dataloaders
    kwargs = {'num_workers': 1, 'pin_memory': True} if use_cuda else {}
    train_loader = torch.utils.data.DataLoader(
        datasets.MNIST('../data', train=True, download=True,
                       transform=transforms.Compose([
                           transforms.ToTensor()
                       ])),
        batch_size=args.batch_size, shuffle=True, **kwargs)

    train_loader_eval = torch.utils.data.DataLoader(
        datasets.MNIST('../data', train=True, transform=transforms.Compose([
                           transforms.ToTensor()
                       ])),
        batch_size=args.test_batch_size, shuffle=True, **{})

    # Init model and optimizer
    model = Encoder(device).to(device)

    #Initialise weights
    model.apply(weights_init)
    optimizer = optim.Adam(model.parameters(), lr=args.lr)

    #Init losses log
    
    prediction_mean_error=[] #Average  rotation prediction error in degrees
    prediction_error_std=[] #Std of error for rotation prediciton
    train_loss=[]

    # Where the magic happens
    for epoch in range(1, args.epochs + 1):
        sys.stdout.write('Epoch {}/{} \n '.format(epoch,args.epochs))
        sys.stdout.flush()
        for batch_idx, (data, targets) in enumerate(train_loader):
            model.train()
            # Reshape data
            data,targets,angles = rotate_tensor(data.numpy(),args.init_rot_range, args.relative_rot_range)
            data = torch.from_numpy(data).to(device)
            targets = torch.from_numpy(targets).to(device)
            angles = torch.from_numpy(angles).to(device)
            angles = angles.view(angles.size(0), 1)

            # Forward passes
            optimizer.zero_grad()
            f_data=model(data) # [N,2,1,1]
            f_targets=model(targets) #[N,2,1,1]

            #Apply rotatin matrix to f_data with feature transformer
            f_data_trasformed= feature_transformer(f_data,angles,device)

            #Define loss

            loss=define_loss(args,f_data_trasformed,f_targets)

            # Backprop
            loss.backward()
            optimizer.step()

            #Log progress
                # if batch_idx % args.log_interval == 0:
                #     sys.stdout.write('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}\r'
                #         .format(epoch, batch_idx * len(data), len(train_loader.dataset),
                #         100. * batch_idx / len(train_loader), loss))
                #     sys.stdout.flush()

            #Store training and test loss
            if batch_idx % args.store_interval==0:
                #Train Lossq
                train_loss.append(evaluate_model(args,model, device, train_loader_eval))

                #Rotation loss in trainign set
                mean, std=rotation_test(args,model, device, train_loader_eval)
                prediction_mean_error.append(mean)
                prediction_error_std.append(std)



    #Save model
    save_model(args,model)
    #Save losses
    train_loss=np.array(train_loss)
    prediction_mean_error=np.array(prediction_mean_error)
    prediction_error_std=np.array(prediction_error_std)

    np.save(path+'/training_loss',train_loss)
    np.save(path+'/prediction_mean_error',prediction_mean_error)
    np.save(path+'/prediction_error_std',prediction_error_std)


    plot_learning_curve(args,train_loss,prediction_mean_error,prediction_error_std,path)


def plot_learning_curve(args,training_loss,average_error,error_std,path):

    x_ticks=np.arange(len(training_loss))*args.store_interval*args.batch_size
    with plt.style.context('ggplot'):
        fig, (ax1,ax2)=plt.subplots(2,1,sharex=True,figsize=(5,5))
        # #Set gray background
        # ax1.set_facecolor('#E6E6E6')
        # ax2.set_facecolor('#E6E6E6')

        #Plot loss
        ax1.plot(x_ticks,training_loss,label='Training Loss',linewidth=1.25)
        loss_type=args.loss+' Loss'
        ax1.set_ylabel(loss_type,fontsize=10)
        
        ax1.legend()

        # #Grid lines
        # ax2.grid()
        # ax1.grid()

       
        line,=ax2.plot(x_ticks,average_error,label='Average Abs training error',linewidth=1.25,color='g')
        ax2.fill_between(x_ticks,average_error-error_std,average_error+error_std,
            alpha=0.2,facecolor=line.get_color(),edgecolor=line.get_color())
        ax2.set_ylabel('Degrees',fontsize=10)
        ax2.set_xlabel('Training Examples',fontsize=10)
        ax2.ticklabel_format(style='sci', axis='x', scilimits=(0,0))
        ax2.legend()


        #Control colour of ticks
        ax1.tick_params(colors='gray', direction='out')
        for tick in ax1.get_xticklabels():
            tick.set_color('gray')
        for tick in ax1.get_yticklabels():
            tick.set_color('gray')

        ax2.tick_params(colors='gray', direction='out')
        for tick in ax2.get_xticklabels():
            tick.set_color('gray')
        for tick in ax2.get_yticklabels():
            tick.set_color('gray')

        fig.suptitle('Learning Curves')
        fig.tight_layout(rect=[0, 0.03, 1, 0.98])
        fig.savefig(path+'/learning_curves')
        fig.clf()
  
if __name__ == '__main__':
    main()
